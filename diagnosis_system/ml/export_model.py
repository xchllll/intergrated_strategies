#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_model.py - 把 sklearn 的 Pipeline(StandardScaler + MLPClassifier) 导出成 JSON

只在 Windows（或任何训练机）上运行，需要 sklearn。板子上不需要这个文件。

用法：
    from export_model import export_pipeline
    export_pipeline(mlp_pipeline, "model/topo_mlp.json",
                    meta={"feature_order": FEATURE_ORDER, "trained_on": "synthetic"})

导出的 JSON 由 json_infer.JsonModel 读取，只需要 numpy。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FORMAT = "dsh-mlp-json/1"


def _find_step(pipeline, class_name):
    for _name, step in getattr(pipeline, "steps", []):
        if type(step).__name__ == class_name:
            return step
    raise ValueError("管线里找不到 %s，实际步骤：%r"
                     % (class_name, [type(s).__name__ for _n, s in getattr(pipeline, "steps", [])]))


def export_pipeline(pipeline, path, meta=None):
    """把已 fit 的管线写成 JSON，返回写入的 Path。"""
    try:
        import sklearn
        version = sklearn.__version__
    except Exception:                       # noqa: BLE001
        version = "unknown"

    scaler = _find_step(pipeline, "StandardScaler")
    mlp = _find_step(pipeline, "MLPClassifier")

    data = {
        "format": FORMAT,
        "created": datetime.now().isoformat(timespec="seconds"),
        "sklearn_version": version,
        "classes": [str(c) for c in mlp.classes_],
        "scaler": {
            "mean": [float(v) for v in scaler.mean_],
            "scale": [float(v) for v in scaler.scale_],
        },
        "mlp": {
            "activation": str(mlp.activation),
            "out_activation": str(mlp.out_activation_),
            # coefs_: 每层一个 (n_in, n_out) 矩阵 → 按层存成 [[[...],[...]], ...]
            "coefs": [
                [[float(v) for v in row] for row in weight] for weight in mlp.coefs_
            ],
            "intercepts": [[float(v) for v in b] for b in mlp.intercepts_],
        },
        "meta": meta or {},
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return path
