
from deep_translator import MyMemoryTranslator

translator = MyMemoryTranslator(
    source="ja-JP",
    target="en-US"
)

texts = [
    "君の声がまだ聞こえる",
    "今日はいい天気ですね。",
    "あなたのことが好きです。",
    "もう戻れないんだね。"
]

for text in texts:
    try:
        result = translator.translate(text)
        print(f"{text} → {result}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
