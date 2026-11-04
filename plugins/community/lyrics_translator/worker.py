"""Isolated CPU translation using installed Argos packages and CTranslate2.

Lyrics already have line boundaries; sentence segmentation is deliberately
unnecessary here. No network calls or automatic model downloads are made.
"""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from collections import deque

PREFIX = "LRCPLUS_ARGOS:"


class Engine:
    def __init__(self, packages):
        self.packages = {}
        self.languages = {}
        self.translators = {}
        for package in packages:
            if getattr(package, "type", "translate") != "translate":
                continue
            if not isinstance(package.from_code, str) or not isinstance(package.to_code, str):
                continue
            self.packages.setdefault((package.from_code, package.to_code), package)
            self.languages[package.from_code] = package.from_name
            self.languages[package.to_code] = package.to_name

    def route(self, source, target):
        if source == target:
            raise ValueError("Choose different source and target languages.")
        queue, visited = deque([(source, [])]), {source}
        while queue:
            current, path = queue.popleft()
            for start, end in sorted(self.packages):
                if start != current or end in visited:
                    continue
                next_path = path + [(start, end)]
                if end == target:
                    return next_path
                visited.add(end)
                queue.append((end, next_path))
        raise ValueError("Install a language model for this pair (or the required intermediate models).")

    def catalog(self):
        routes = []
        for source in sorted(self.languages):
            for target in sorted(self.languages):
                try:
                    path = self.route(source, target)
                except ValueError:
                    continue
                routes.append({"source": source, "target": target,
                               "via": [end for start, end in path[:-1]]})
        return {"languages": [{"code": code, "name": name}
                              for code, name in sorted(self.languages.items())], "routes": routes}

    def step(self, pair, text):
        import ctranslate2

        package = self.packages[pair]
        if pair not in self.translators:
            self.translators[pair] = ctranslate2.Translator(
                str(package.package_path / "model"), device="cpu", compute_type="int8",
                inter_threads=1, intra_threads=2,
            )
        prefix = getattr(package, "target_prefix", "")
        kwargs = {"target_prefix": [[prefix]]} if prefix else {}
        result = self.translators[pair].translate_batch(
            [package.tokenizer.encode(text)], beam_size=4, **kwargs,
        )[0].hypotheses[0]
        if prefix and result and result[0] == prefix:
            result = result[1:]
        return package.tokenizer.decode(result).strip()

    def translate(self, source, target, rows, progress):
        route = self.route(source, target)
        output, cached, seen = {}, {}, set()
        for number, row in enumerate(rows, 1):
            index, text = row.get("index"), row.get("text")
            if type(index) is not int or index in seen or not isinstance(text, str) or not text.strip():
                raise ValueError("Invalid or duplicate lyric row.")
            seen.add(index)
            if text not in cached:
                translated = text
                for pair in route:
                    translated = self.step(pair, translated)
                    if not translated:
                        raise ValueError(f"No translation was returned for lyric row {index + 1}.")
                cached[text] = translated
            output[index] = cached[text]
            progress(number, len(rows))
        return {"translations": output}


def run(request, progress):
    folder = Path(request["folder"]).expanduser().resolve()
    os.environ.update(ARGOS_PACKAGES_DIR=str(folder), ARGOS_DEVICE_TYPE="cpu",
                      ARGOS_MODEL_PROVIDER="OPENNMT", ARGOS_CHUNK_TYPE="NONE")
    # Keep Argos's config/cache alongside the plugin's chosen model folder.
    for env, suffix in (("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
        os.environ[env] = str(folder.parent / ("argos-" + suffix))
    from argostranslate import package

    if request["action"] == "install":
        model = Path(request["model_file"]).resolve()
        if not model.is_file() or model.suffix.lower() != ".argosmodel":
            raise ValueError("Choose an .argosmodel language package.")
        # Stage extraction away from the installed packages. Cancellation or a
        # bad archive must not leave a half-installed model in the catalog.
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".argos-install-", dir=folder.parent) as stage:
            old_dir, old_dirs = package.settings.package_data_dir, package.settings.package_dirs
            try:
                package.settings.package_data_dir = Path(stage)
                package.settings.package_dirs = [Path(stage)]
                package.install_from_path(model)
                installed = package.get_installed_packages()
                if len(installed) != 1 or not (installed[0].package_path / "model").is_dir():
                    raise ValueError("The archive must contain one translation model.")
                destination = folder / installed[0].package_path.name
                if destination.exists():
                    raise ValueError("This model is already installed. Choose a different package or models folder.")
                installed[0].package_path.rename(destination)
            finally:
                package.settings.package_data_dir, package.settings.package_dirs = old_dir, old_dirs
    engine = Engine(package.get_installed_packages())
    if request["action"] in {"catalog", "install"}:
        return engine.catalog()
    if request["action"] != "translate":
        raise ValueError("Unknown translation action.")
    return engine.translate(request["source"], request["target"], request["rows"], progress)


def main():
    stream = sys.stdout

    def emit(event):
        stream.write(PREFIX + json.dumps(event, ensure_ascii=True) + "\n")
        stream.flush()

    try:
        request = json.loads(sys.stdin.readline())
        for path in request.get("python_paths", []):
            if path and path not in sys.path:
                sys.path.append(path)
        with contextlib.redirect_stdout(sys.stderr):
            result = run(request, lambda done, total: emit({"progress": [done, total]}))
        emit({"result": result})
    except Exception as error:
        emit({"error": str(error) or type(error).__name__})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
