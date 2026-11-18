# -*- coding: utf-8 -*-
"""Bin izleyici ve bin peyci. Acik mezat varsa pey yazilmaz.

Ornek:
  python scripts/stress_test_1000.py --hedef http://127.0.0.1:5000 --sure 15
  python scripts/stress_test_1000.py --hedef http://127.0.0.1:5000 --izleyici 500 --peyci 500
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stress_test_write import main  # noqa: E402


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--izleyici" not in argv:
        argv = ["--izleyici", "1000"] + argv
    if "--peyci" not in argv:
        argv = ["--peyci", "1000"] + argv
    sys.argv = [sys.argv[0]] + argv
    main()
