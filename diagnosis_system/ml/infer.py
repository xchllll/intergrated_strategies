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

# 核心函数：一定要叫这个名字！
def predict_topology(sweep_dict):
    model = get_model()
    
    # 提取特征
    feats = extract_features(sweep_dict["freqs"], sweep_dict["mag_db"], sweep_dict["phase_deg"])
    X_input = to_vector(feats).reshape(1, -1) 
    
    # 预测
    pred_label = model.predict(X_input)[0]
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
        
    return {
        "label": label_cn,
        "confidence": confidence,
        "top3": top3
    }