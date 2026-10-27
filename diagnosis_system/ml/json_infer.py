#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""json_infer.py - 不依赖 scikit-learn 的推理（读取 export_model.py 导出的 JSON）

为什么要这个文件：
    sklearn 的 .joblib 是 Python 对象的快照，里面记录了 sklearn 的类路径和版本。
    Windows 上训练用的版本（1.9.x）和 PYNA-Q2 板子上的版本（0.23.2）不一致时，
    joblib.load 可能直接报错。把模型转成 JSON 后，板子上只需要 numpy 就能推理，
    彻底摆脱版本耦合。

JSON 结构（dsh-mlp-json/1）：
    {
      "format": "dsh-mlp-json/1",
      "classes": ["rc_lowpass", ...],                 # 6 个类别，顺序 = 输出列顺序
      "scaler": {"mean": [...13], "scale": [...13]},  # StandardScaler 的两个参数
      "mlp": {"activation": "relu", "out_activation": "softmax",
              "coefs": [[...],[...],[...]],           # 3 层权重：13->32, 32->16, 16->6
              "intercepts": [[...],[...],[...]]},     # 每层偏置
      "meta": {"feature_order": [...], "trained_on": "synthetic", ...}
    }

用法：
    from json_infer import JsonModel
    model = JsonModel("model/topo_mlp.json")
    proba = model.predict_proba(X)        # (n, 6)
    labels = model.predict(X)             # (n,)
"""
from __future__ import annotations

import json

import numpy as np

FORMAT = "dsh-mlp-json/1"


class JsonModel:
    """只依赖 numpy 的前向推理，接口与 sklearn 的 Pipeline 保持一致。"""

    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("format") != FORMAT:
            raise ValueError("模型格式不支持：%r（需要 %s）" % (data.get("format"), FORMAT))

        self.path = str(path)
        self.classes_ = np.asarray([str(c) for c in data["classes"]], dtype=object)
        self.mean_ = np.asarray(data["scaler"]["mean"], dtype=np.float64)
        self.scale_ = np.asarray(data["scaler"]["scale"], dtype=np.float64)
        self.coefs_ = [np.asarray(w, dtype=np.float64) for w in data["mlp"]["coefs"]]
        self.intercepts_ = [np.asarray(b, dtype=np.float64) for b in data["mlp"]["intercepts"]]
        self.activation = data["mlp"].get("activation", "relu")
        self.out_activation = data["mlp"].get("out_activation", "softmax")
        self.meta = data.get("meta", {})

        if self.activation != "relu":
            raise ValueError("本实现只支持 relu 隐层，模型里是 %r" % (self.activation,))

    # -- 内部 ---------------------------------------------------------------
    @staticmethod
    def _relu(x):
        return np.maximum(x, 0.0)

    @staticmethod
    def _softmax(z):
        z = z - np.max(z, axis=1, keepdims=True)
        e = np.exp(z)
        return e / np.sum(e, axis=1, keepdims=True)

    @staticmethod
    def _sigmoid(z):
        return 1.0 / (1.0 + np.exp(-z))

    # -- 对外接口（与 sklearn 同名）------------------------------------------
    def predict_proba(self, X):
        a = np.atleast_2d(np.asarray(X, dtype=np.float64))
        a = (a - self.mean_) / self.scale_

        last = len(self.coefs_) - 1
        for index, (weight, bias) in enumerate(zip(self.coefs_, self.intercepts_)):
            a = a @ weight + bias
            if index != last:
                a = self._relu(a)

        if self.out_activation == "softmax":
            return self._softmax(a)
        if self.out_activation == "logistic":
            p = self._sigmoid(a)
            return np.hstack([1.0 - p, p])
        raise ValueError("不支持输出激活函数 %r" % (self.out_activation,))

    def predict(self, X):
        proba = self.predict_proba(X)
        return self.classes_[np.argmax(proba, axis=1)]
