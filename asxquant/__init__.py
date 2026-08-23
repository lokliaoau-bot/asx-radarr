# -*- coding: utf-8 -*-
"""ASX Market Radar -- probabilistic outlook, sector fund flow and short-side positioning.

Thread caps are set before numpy/sklearn are imported anywhere: the fits here run on
~1000x40 matrices, where BLAS/OpenMP thread hand-off costs more than the arithmetic
(measured ~22x slower with default threading).
"""
import os as _os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    _os.environ.setdefault(_v, "1")

__version__ = "1.0.0"
