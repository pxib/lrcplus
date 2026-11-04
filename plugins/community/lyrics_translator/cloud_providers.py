"""Official REST request/response adapters. No lyric or credential logging."""
import html
import json
from urllib.parse import urlencode

PROVIDERS = {
    "azure": "Azure AI Translator (untested, paid)",
    "google": "Google Cloud Translation (untested, paid)",
    "deepl": "DeepL API (accurate, paid)",
    "mymemory": "MyMemory (free, limited, questionable quality)",
}
# A deliberately small common language list; provider codes stay adapter-owned.
LANGUAGES = {"ja": "Japanese", "en": "English", "zh": "Chinese (Simplified)",
             "zh-Hant": "Chinese (Traditional)", "ko": "Korean", "de": "German",
             "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
             "ru": "Russian", "nl": "Dutch", "pl": "Polish", "vi": "Vietnamese"}


def catalog():
    return {"languages": [{"code": code, "name": name} for code, name in LANGUAGES.items()],
            "routes": [{"source": a, "target": b, "via": []} for a in LANGUAGES for b in LANGUAGES if a != b]}


def language_code(provider, code, *, target=False):
    if provider == "azure":
        return {"zh": "zh-Hans"}.get(code, code)
    if provider == "deepl":
        if code.startswith("zh"):
            return ("ZH-HANT" if code == "zh-Hant" else "ZH-HANS") if target else "ZH"
        return code.upper()
    return {"zh": "zh-CN", "zh-Hant": "zh-TW"}.get(code, code)


def batches(provider, rows):
    """Validate all input before sending anything; deduplicate repeated choruses."""
    indices, unique = set(), {}
    for row in rows:
        index, text = row["index"], row["text"]
        if type(index) is not int or index in indices or not isinstance(text, str) or not text.strip():
            raise ValueError("Translation rows need unique integer indices and nonempty text.")
        indices.add(index)
        if provider == "mymemory" and len(text.encode("utf-8")) > 500:
            raise ValueError("MyMemory accepts at most 500 UTF-8 bytes per lyric row. Choose another provider for longer rows.")
        if len(text) > 5000:
            raise ValueError("A lyric row exceeds this integration's 5,000-character request limit.")
        unique.setdefault(text, []).append(index)
    groups, group, size = [], [], 0
    for text, row_indices in unique.items():
        if group and (provider == "mymemory" or len(group) >= 40 or size + len(text.encode("utf-8")) > 32000):
            groups.append(group)
            group, size = [], 0
        group.append((text, row_indices))
        size += len(text.encode("utf-8"))
    if group:
        groups.append(group)
    return groups


def request(provider, source, target, texts, settings):
    source = language_code(provider, source)
    target = language_code(provider, target, target=True)
    key = settings.get("api_key", "")
    if provider != "mymemory" and not key:
        raise ValueError("Enter an API key for the selected provider.")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if provider == "azure":
        url = "https://api.cognitive.microsofttranslator.com/translate?" + urlencode({
            "api-version": "3.0", "from": source, "to": target})
        headers["Ocp-Apim-Subscription-Key"] = key
        if settings.get("region"):
            headers["Ocp-Apim-Subscription-Region"] = settings["region"]
        body = [{"Text": text} for text in texts]
    elif provider == "google":
        url = "https://translation.googleapis.com/language/translate/v2"
        headers["X-Goog-Api-Key"] = key
        body = {"q": texts, "source": source, "target": target, "format": "text"}
    elif provider == "deepl":
        plan = settings.get("plan", "auto")
        use_free = plan == "free" or (plan == "auto" and key.endswith(":fx"))
        host = "api-free.deepl.com" if use_free else "api.deepl.com"
        url = f"https://{host}/v2/translate"
        headers["Authorization"] = "DeepL-Auth-Key " + key
        body = {"text": texts, "source_lang": source, "target_lang": target}
    elif provider == "mymemory":
        url = "https://api.mymemory.translated.net/get?" + urlencode({"q": texts[0], "langpair": source + "|" + target})
        return "GET", url, {}, b""
    else:
        raise ValueError("Unknown translation provider.")
    return "POST", url, headers, json.dumps(body, ensure_ascii=False).encode("utf-8")


def response(provider, data, expected):
    try:
        if provider == "azure":
            values = [item["translations"][0]["text"] for item in data]
        elif provider == "google":
            values = [html.unescape(item["translatedText"]) for item in data["data"]["translations"]]
        elif provider == "deepl":
            values = [item["text"] for item in data["translations"]]
        else:
            if str(data["responseStatus"]) != "200" or data.get("quotaFinished"):
                raise ValueError("MyMemory rejected the request or its daily quota is exhausted.")
            values = [html.unescape(data["responseData"]["translatedText"])]
        if len(values) != expected or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("The provider returned incomplete translations.")
        return values
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("The provider returned an invalid translation response.") from error
