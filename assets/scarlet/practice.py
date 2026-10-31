"""Development launcher: python -m assets.scarlet.practice --spell 7."""

import argparse

from PySide6.QtWidgets import QApplication

from assets.scarlet.danmaku_game import DanmakuWindow
from assets.scarlet.flandre import PHASES, SPELLS


def main():
    parser = argparse.ArgumentParser(description="Scarlet Refrain boss practice (does not save high scores)")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--spell", type=int, choices=range(1, 11), help="Start a named spell (1-10)")
    selection.add_argument("--phase", choices=[p.id for p in PHASES], help="Start a non-spell or spell by id")
    parser.add_argument("--seed", type=int, default=495)
    parser.add_argument("--difficulty", choices=("Normal", "Lunatic"), default="Normal")
    parser.add_argument("--invulnerable", action="store_true")
    parser.add_argument("--slow", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    window = DanmakuWindow(debug=True)
    if args.difficulty != window.game.difficulty:
        window._change_difficulty(args.difficulty)
    phase = SPELLS[args.spell - 1].id if args.spell else args.phase or "cranberry"
    window.game.start_phase(phase, seed=args.seed)
    window.game.debug_invulnerable = args.invulnerable
    window.game.debug_speed = .25 if args.slow else 1
    window.scene.story_phase, window.scene.boss_position = "combat", None
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
