"""Reimu's Extra-stage dialogue, in Japanese and English as supplied by the user.

Source: https://en.touhouwiki.net/wiki/Embodiment_of_Scarlet_Devil/Story/Reimu%27s_Extra
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DialogueLine:
    speaker: str
    ja: str
    en: str
    start_bgm: bool = False

    def text(self, language):
        return self.en if language == "en" else self.ja


NAMES = {
    "flandre": {"ja": "フランドール・スカーレット", "en": "Flandre"},
    "reimu": {"ja": "博麗 霊夢", "en": "Reimu"},
}

INTRO_DIALOGUE = (
    DialogueLine("flandre", "お待たせ。", "Sorry to keep you waiting."),
    DialogueLine("reimu", "誰？前来た時は居なかったような\n気がするけど⋯⋯",
                 "Who are you? I don't think you were here when I came\nlast time..."),
    DialogueLine("flandre", "居たけど、見えなかったの。\n\nで、あなたはもしかして人間？",
                 "I was, but you couldn't see me.\nSo, are you a human?"),
    DialogueLine("reimu", "ああ、そうよ。", "Yes, that's right."),
    DialogueLine("flandre", "騙したりしてない？\n\n人間って飲み物の形でしか見た事\n無いの。",
                 "You're not trying to trick me, are you?\n\nI've only ever seen humans\nin the form of a drink."),
    DialogueLine("reimu", "ああ、人間だよ。\n\n人間は、紅茶よりは複雑な物。\nなのよ。殆どの人はね。",
                 "Yes, I'm actually human.\n\nHumans are a bit more complicated than black tea.\nMost of them are, anyway."),
    DialogueLine("flandre", "ほら、鶏って。", "But, chickens..."),
    DialogueLine("reimu", "あれ？", "Huh?"),
    DialogueLine("flandre", "捌いたり出来ない人でも⋯\n美味しく頂けるの。",
                 "...even if you don't know how to cook them,\nthey're still tasty."),
    DialogueLine("reimu", "あんたん所は、人間を誰が\n捌くの？",
                 "And who makes food out of humans\nin your household?"),
    DialogueLine("flandre", "さー？お姉様が遣ってる⋯\nわけ無いし⋯",
                 "I dunno? I don't think\nmy sister does..."),
    DialogueLine("reimu", "お姉様？\n\nレプリカとか言う、悪魔の事？",
                 "Sister?\n\nReplica-somethingorother, the devil?"),
    DialogueLine("flandre", "レミリア！\nレミリアお姉様よ。", "Remilia!\nMy sister Remilia."),
    DialogueLine("reimu", "アイツは絶対に、調理は出来ない\nと思うよ。",
                 "I doubt that your sister\ncan cook at all."),
    DialogueLine("flandre", "しないわー。", "She doesn't."),
    DialogueLine("reimu", "妹君に言いたいけど。\n\nお姉様はいつも家の神社に入り\n浸って迷惑なの⋯\n\n何とか言って遣ってよ。",
                 "There's something I want to tell you.\n\nYour older sister keeps intruding on my shrine\nand being a nuisance.\n\nCould you do something about it?"),
    DialogueLine("flandre", "知ってるわよ。\n\n私も行こうとしたら・・・", "I know.\n\nI wanted to go too..."),
    DialogueLine("reimu", "するな。", "Don't."),
    DialogueLine("flandre", "止められた。\nお外は豪雨で歩けない。", "I couldn't.\nIt was raining too hard."),
    DialogueLine("reimu", "要注意人物なんだね。\n過去に何か遣らかしたの？",
                 "You've got special needs, it seems.\nDid you do something bad in the past?"),
    DialogueLine("flandre", "何も出来る筈がないわ。\n\n私は４９５年間一回も、お外に出て\nないのよ。",
                 "I couldn't have done anything.\n\nI haven't gone out even once\nin the past 495 years."),
    DialogueLine("reimu", "ホントに、問題児なのね。", "A real problem child."),
    DialogueLine("flandre", "其処に飛び込む遊び道具⋯", "I have a play toy over here...", start_bgm=True),
    DialogueLine("reimu", "何して遊ぶ？", "What do you want to play?"),
    DialogueLine("flandre", "弾幕ごっこ。", "The danmaku game."),
    DialogueLine("reimu", "ああ、パターン作りごっこね。\nそれは私の得意分野だわ。",
                 "Oh, the pattern-making game.\nI'm pretty good at that."),
)

OUTRO_DIALOGUE = (
    DialogueLine("reimu", "見た？\nこれが神に仕える物の力なのよ！",
                 "See?\nThis is the power of those who serve gods!"),
    DialogueLine("flandre", "そんなんで勝ったと思っちゃうんだ。\n\nまだまだこれからよ！",
                 "Don't go thinking you won with that.\n\nThere's still more to come!"),
    DialogueLine("reimu", "！？\n\nって、もうそんな余裕も無い癖に。", "!?\n\nEr, you're in really bad shape though."),
    DialogueLine("flandre", "はい、もう、煙も出ません。", "Yes, I can't even shoot smoke anymore..."),
    DialogueLine("reimu", "何時でも遊びに来てあげるから。\n\n頼むから、神社に遊びに来ないで。",
                 "I'll come play whenever you want.\n\nSo please, don't come to the shrine."),
    DialogueLine("flandre", "あら、お礼にケーキと紅茶を\n持って行こうと思ってたのに。",
                 "Oh, and I was thinking of visiting you\nwith cake and tea, as thanks."),
    DialogueLine("reimu", "あんたの所の食べ物は人間の所に\nもってきちゃ駄目だって。",
                 "Try not to bring your kind\nof food to a human's place."),
    DialogueLine("flandre", "甘さ控えめよ？", "It's not too sweet, is it?"),
    DialogueLine("reimu", "ノンシュガーでも！\n\n良い子は大人しく帰って\n寝てなさい。",
                 "Not even if it's sugar-free!\n\nBe a good girl. Go back home\nand go to sleep."),
    DialogueLine("flandre", "⋯此処は私の家よ？", "...But this is my house."),
    DialogueLine("reimu", "じゃぁ帰らなくてもええわ。\n悪い子は。\n\n私はそろそろ帰るけど。",
                 "So you don't need to go home.\nFitting for a bad girl.\n\nI'm going home now."),
    DialogueLine("flandre", "悪い子は、帰らなくてもいいのよ。", "Bad girls don't need to go home."),
    DialogueLine("reimu", "・・・神社にもう一人悪い子\n置いてきたから。", "...I left another bad girl\nat the shrine, after all."),
    DialogueLine("flandre", "悪い子って、誰の事？", "Who do you mean, bad girl?"),
    DialogueLine("reimu", "あんたと姉さんだ！！", "You and your sister!!"),
)

BGM_CUE_INDEX = next(index for index, line in enumerate(INTRO_DIALOGUE) if line.start_bgm)
