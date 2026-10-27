import sys
import os
import joblib
import numpy as np
from pathlib import Path

# 把根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.features import extract_features, to_vector
from ml.gen_mock import CLASSES

# 全局变量
_model = None

def get_model():
    global _model
    if _model is None:
        model_path = os.path.join(os.path.dirname(__file__), "model", "topo_mlp.joblib")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"找不到模型文件: {model_path}，请先运行 train.py")
        _model = joblib.load(model_path)
    return _model

def predict_topology(sweep_dict, data_source="demo"):
    """
    核心推理函数。增加了进门校验，防止在无效数据上强行给出高置信度。
    data_source: "rtl" | "audio" | "demo" | "pynq_rtl" | "pynq_audio_loopback"
    """
    # ==========================================
    # C: 进门校验（防止一根直线或者断线测出来的数据让模型乱猜）
    # ==========================================
    freqs = sweep_dict.get("freqs", [])
    mags = sweep_dict.get("mag_db", [])
    phases = sweep_dict.get("phase_deg", [])

    # 1. 检查点数是否太少
    if len(freqs) < 10 or len(mags) < 10:
        return _invalid_response("数据点太少（<10个），无法提取有效特征", data_source)

    # 2. 检查是否包含 NaN 或 Infinity
    if np.any(np.isnan(mags)) or np.any(np.isinf(mags)):
        return _invalid_response("数据包含无效值 (NaN/Inf)，请检查硬件连接", data_source)

    # 3. 检查动态范围（如果曲线太平，说明没接电路或者短路了）
    mags_arr = np.array(mags)
    dynamic_range = np.max(mags_arr) - np.min(mags_arr)
    if dynamic_range < 1.0:
        return _invalid_response(f"信号动态范围过小 ({dynamic_range:.2f} dB)，可能未接入电路或已削顶", data_source)

    # ==========================================
    # 正常推理
    # ==========================================
    model = get_model()
    
    # 提取特征
    feats = extract_features(freqs, mags, phases)
    X_input = to_vector(feats).reshape(1, -1) 
    
    # 预测
    pred_proba = model.predict_proba(X_input)[0]
    
    max_idx = np.argmax(pred_proba)
    confidence = float(pred_proba[max_idx])
    
    label_en = model.classes_[max_idx]
    label_cn = CLASSES.get(label_en, label_en)
    
    top3_indices = np.argsort(pred_proba)[::-1][:3]
    top3 = []
    for idx in top3_indices:
        top3.append({
            "label": CLASSES.get(model.classes_[idx], model.classes_[idx]),
            "confidence": float(pred_proba[idx])
        })
        
    # 统一处理 model_note (兼容 app.py 传过来的多种 mode 字符串)
    if data_source in ["rtl", "pynq_rtl"]:
        note = "基于自研 PL 解调实测数据"
    elif data_source in ["audio", "pynq_audio_loopback"]:
        note = "基于官方音频闭环数据，自研解调链已接入但未用于本次推理"
    else:
        note = "演示 CSV + 预训练演示模型，不代表硬件实测"

    return {
        "label": label_cn,
        "confidence": confidence,
        "top3": top3,
        "model_note": note
    }

def _invalid_response(reason, data_source):
    """封装返回无效数据的结构，避免前端报错"""
    if data_source in ["rtl", "pynq_rtl"]:
        note = "基于自研 PL 解调实测数据"
    elif data_source in ["audio", "pynq_audio_loopback"]:
        note = "基于官方音频闭环数据"
    else:
        note = "演示 CSV 数据"
        
    return {
        "label": "数据无效",
        "confidence": 0.0,
        "top3": [],
        "model_note": f"【拦截】{reason}。{note}。"
    }