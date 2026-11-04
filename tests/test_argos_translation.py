from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.plugin_manager import PluginManager
from core.translation_document import render_translations, snapshot_document
from lyrics.lrcx_parser import parse_lrcx
from plugins.community.lyrics_translator.worker import Engine


@pytest.fixture
def document(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    lyrics = tmp_path / "song.lrcx"
    lyrics.write_text(
        "[ar:Artist]\n[g:pinyin]\n[offset:125]\n"
        r"[00:01.000]> {君|きみ}<00:02.000>**の**<00:03.000>" + "\n"
        r"[00:04.000]Existing{\nt}Provided" + "\n"
        "[00:05.000]\n"
        r"[00:06.000]Plain {海|うみ}" + "\n"
        "[00:06.000]Same timestamp\n", encoding="utf-8")
    return snapshot_document(audio, lyrics)


def test_document_translation_preserves_ruby_formatting_timing_and_existing_rows(document):
    original = deepcopy(document)
    before = parse_lrcx(document["lrcx"], infer_ends=False)
    rendered = render_translations(document, {0: "*Literal*\nSecond row", 3: "Sea", 4: "Same time"})
    after = parse_lrcx(rendered, infer_ends=False)
    assert document == original
    assert "[ar:Artist]" in rendered and "[offset:125]" in rendered and "[g:pinyin]" in rendered
    assert len(before) == len(after) == 5
    assert after[0].text == "君の\n*Literal*\nSecond row"
    assert after[0].translation_lines == [1, 2]
    assert after[0].segments[:len(before[0].segments)] == before[0].segments
    assert after[0].style_spans == before[0].style_spans
    assert after[0].is_blockquote and after[0].end == 3
    assert after[1] == before[1]
    assert after[2] == before[2]
    assert after[3].text == "Plain 海\nSea"
    assert after[3].segments[0].ruby_spans == before[3].segments[0].ruby_spans
    assert after[4].text == "Same timestamp\nSame time" and after[4].start == after[3].start
    assert not after[3].has_karaoke


@pytest.mark.parametrize("values", [{1: "Replace"}, {500: "Unknown"}, {True: "Boolean index"}, {0: ""}])
def test_invalid_or_existing_translation_rows_are_rejected(document, values):
    with pytest.raises(ValueError):
        render_translations(document, values)


def test_public_api_saves_separate_copy_for_captured_song(document, tmp_path):
    manager = PluginManager()
    manager._host_window = SimpleNamespace(current_audio_path=Path(document["audio_path"]),
                                          current_lyrics_path=Path(document["lyrics_path"]))
    api = manager.get_api("lyrics_translator")
    assert api.version == 6
    snapshot = api.get_current_lyrics_document()
    assert snapshot == document
    original_bytes = Path(document["lyrics_path"]).read_bytes()
    attached = []
    manager.attach_lyrics = lambda audio, lyrics: attached.append((audio, lyrics))
    manager._host_window.current_audio_path = tmp_path / "different.mp3"
    output = tmp_path / "song.en.machine.lrcx"
    assert api.save_lyrics_translations(document, {0: "Translation"}, str(output)) == str(output.resolve())
    assert attached == [(document["audio_path"], str(output.resolve()))]
    assert Path(document["lyrics_path"]).read_bytes() == original_bytes
    assert parse_lrcx(output.read_text(encoding="utf-8"))[0].translation_lines == [1]
    with pytest.raises(ValueError, match="separate"):
        api.save_lyrics_translations(document, {0: "Translation"}, document["lyrics_path"])
    assert Path(document["lyrics_path"]).read_bytes() == original_bytes


def package(source, target):
    return SimpleNamespace(from_code=source, to_code=target, from_name=source.upper(),
                           to_name=target.upper(), type="translate")


def test_engine_prefers_direct_models_and_reports_pivot_routes():
    engine = Engine([package("ja", "en"), package("en", "zh"), package("ja", "zh")])
    assert engine.route("ja", "zh") == [("ja", "zh")]
    pivot = Engine([package("ja", "en"), package("en", "zh")])
    assert pivot.route("ja", "zh") == [("ja", "en"), ("en", "zh")]
    assert {"source": "ja", "target": "zh", "via": ["en"]} in pivot.catalog()["routes"]
    with pytest.raises(ValueError):
        pivot.route("zh", "ja")


def test_engine_preserves_indices_and_reuses_repeated_text(monkeypatch):
    engine = Engine([package("ja", "en"), package("en", "zh")])
    calls, progress = [], []
    def step(pair, text):
        calls.append((pair, text))
        return text + "-" + pair[1]
    monkeypatch.setattr(engine, "step", step)
    result = engine.translate("ja", "zh", [{"index": 3, "text": "A"}, {"index": 7, "text": "A"},
                                             {"index": 8, "text": "B"}], lambda *value: progress.append(value))
    assert result["translations"] == {3: "A-en-zh", 7: "A-en-zh", 8: "B-en-zh"}
    assert len(calls) == 4 and progress == [(1, 3), (2, 3), (3, 3)]


def test_native_engine_uses_cpu_model_tokenizer_without_sbd(monkeypatch, tmp_path):
    import sys
    calls = []
    class Translator:
        def __init__(self, path, **options):
            calls.append((path, options))
        def translate_batch(self, values, **options):
            assert values == [["Original"]]
            assert options == {"beam_size": 4, "target_prefix": [["<zh>"]]}
            return [SimpleNamespace(hypotheses=[["<zh>", "Translated"]])]
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(Translator=Translator))
    model = package("en", "zh")
    model.package_path = tmp_path
    model.target_prefix = "<zh>"
    model.tokenizer = SimpleNamespace(encode=lambda text: [text], decode=lambda tokens: "".join(tokens))
    engine = Engine([model])
    assert engine.step(("en", "zh"), "Original") == "Translated"
    assert engine.step(("en", "zh"), "Original") == "Translated"
    assert calls == [(str(tmp_path / "model"), {"device": "cpu", "compute_type": "int8", "inter_threads": 1, "intra_threads": 2})]


def test_plugin_manifest_is_optional_and_quality_label_is_visible():
    from core.dependencies import parse_dependencies

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "plugins/community/lyrics_translator/manifest.json").read_text())
    assert manifest["api_version"] == 6
    assert manifest["name"] == "Lyrics Translator"
    assert manifest["dependencies"][0]["requirement"] == "argostranslate==1.11.0"
    dependencies = parse_dependencies(manifest["dependencies"])
    assert dependencies[0].import_name == "argostranslate"
    assert dependencies[0].optional


@pytest.mark.parametrize("case", ["valid", "invalid", "already_installed"])
def test_model_installation_commits_complete_packages_only(tmp_path, monkeypatch, case):
    import os
    import sys
    import zipfile
    from plugins.community.lyrics_translator.worker import run

    folder = tmp_path / "models"
    folder.mkdir()
    settings = SimpleNamespace(package_data_dir=folder, package_dirs=[folder])
    def installed():
        result = []
        for child in settings.package_dirs[0].iterdir():
            if child.is_dir():
                model = package("ja", "en")
                model.package_path = child
                result.append(model)
        return result
    def install(path):
        with zipfile.ZipFile(path) as archive:
            archive.extractall(settings.package_data_dir)
    fake = SimpleNamespace(settings=settings, install_from_path=install, get_installed_packages=installed)
    monkeypatch.setitem(sys.modules, "argostranslate", SimpleNamespace(package=fake))
    for name in ("ARGOS_PACKAGES_DIR", "ARGOS_DEVICE_TYPE", "ARGOS_MODEL_PROVIDER", "ARGOS_CHUNK_TYPE",
                 "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    archive = tmp_path / "model.argosmodel"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("translate-ja_en/metadata.json", "{}")
        if case != "invalid":
            output.writestr("translate-ja_en/model/model.bin", "new")
    if case == "already_installed":
        model = folder / "translate-ja_en/model"
        model.mkdir(parents=True)
        (model / "model.bin").write_text("original")
    request = {"action": "install", "folder": str(folder), "model_file": str(archive)}
    if case == "valid":
        assert run(request, lambda *args: None)["routes"]
        assert (folder / "translate-ja_en/model/model.bin").read_text() == "new"
    else:
        with pytest.raises(ValueError):
            run(request, lambda *args: None)
        if case == "already_installed":
            assert (folder / "translate-ja_en/model/model.bin").read_text() == "original"
        else:
            assert list(folder.iterdir()) == []
    assert settings.package_data_dir == folder and settings.package_dirs == [folder]
    assert not list(tmp_path.glob(".argos-install-*"))
