"""Exercise native Windows font allocation in an isolated, untraced process."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows font allocation")
def test_missing_preview_font_does_not_load_large_native_fallback_cache(tmp_path):
    script = textwrap.dedent(r'''
        import json
        from pathlib import Path
        import sys
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        from core.lyrics_settings import save_global_lyrics_settings
        from tools.profile_memory import process_memory
        from ui.lrc_editor import LrcEditorDialog
        import ui.lrc_editor as editor_module
        import lyrics.furigana_widget as renderer_module

        app = QApplication([])
        LrcEditorDialog._schedule_auto_reading_generation = lambda self: None
        LrcEditorDialog._start_background_reading_generation = lambda *args, **kwargs: None
        editor_module.get_cached_furigana_segments = lambda text: [{'text': text}]
        renderer_module.get_cached_furigana_segments = editor_module.get_cached_furigana_segments
        path = Path(sys.argv[1]) / 'preview.lrc'
        path.write_text('[00:01.000]\u6c88\u3080\u3088\u3046\u306b\u6eb6\u3051\u3066\u3086\u304f\u3088\u3046\u306b\n', encoding='utf-8')
        baseline = LrcEditorDialog(path)
        baseline.grab()
        baseline.close()
        baseline.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        del baseline

        save_global_lyrics_settings({'font_family': 'LRCPlus Missing Preview Font 8491',
                                    'show_ruby': False, 'show_romaji': True})
        before_working, before_private = process_memory()
        editor = LrcEditorDialog(path)
        editor.grab()
        after_working, after_private = process_memory()
        print(json.dumps({'working_delta': after_working - before_working,
                          'private_delta': after_private - before_private}))
        editor.close()
    ''')
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONIOENCODING="utf-8",
               LRCPLUS_SETTINGS_PATH=str(tmp_path / "settings.json"),
               LRCPLUS_DATA_DIR=str(tmp_path / "data"))
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout.strip().splitlines()[-1])
    # Missing faces previously added approximately 68 MiB of working set and
    # 35 MiB of private memory. Leave ample room for another editor's widgets.
    assert measured["working_delta"] < 20 * 1048576, measured
    assert measured["private_delta"] < 16 * 1048576, measured
