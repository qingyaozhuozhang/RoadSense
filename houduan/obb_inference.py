"""YOLO ONNX detection / OBB postprocessing (independent of onnxruntime).

Ultralytics YOLOv8 OBB raw output: [1, 4 + class_count + 1, 8400],
where final channel is rotation in radians. Do NOT treat it as class score.
"""
import math
import cv2
import numpy as np


def decode_predictions(raw, class_count, rotated, scale, padding, frame_size,
                       conf=0.3, iou=0.5, max_candidates=400):
    """Return [{corners: (4,2), class_id, confidence, area}] in image pixels.

    Accepts (1, C, N), (C,N), or (N,C) raw YOLO outputs, without prebuilt NMS.
    """
    pred = np.asarray(raw)
    if pred.ndim == 3 and pred.shape[0] == 1:
        pred = pred[0]
    if pred.ndim != 2:
        raise ValueError(f'无法识别 YOLO 输出维度: {pred.shape}')
    channels = 4 + class_count + int(rotated)
    if pred.shape[0] == channels:
        pred = pred.T
    if pred.shape[1] != channels:
        raise ValueError(f'模型通道数 {pred.shape} 与类别数 {class_count}、旋转任务 {rotated} 不一致')
    scores = pred[:, 4:4 + class_count]
    classes = scores.argmax(axis=1)
    confidence = scores[np.arange(len(scores)), classes]
    indices = np.flatnonzero(confidence >= conf)
    if indices.size == 0:
        return []
    if indices.size > max_candidates:
        indices = indices[np.argpartition(confidence[indices], -max_candidates)[-max_candidates:]]
    rows = pred[indices]
    scores_keep = confidence[indices]
    clss = classes[indices]
    left, top = padding
    h, w = frame_size
    boxes = rows[:, :4].astype(np.float64).copy()
    boxes[:, 0] = (boxes[:, 0] - left) / scale
    boxes[:, 1] = (boxes[:, 1] - top) / scale
    boxes[:, 2:] /= scale
    boxes[:, 2:] = np.maximum(boxes[:, 2:], 0)
    valid = np.isfinite(boxes).all(axis=1) & (boxes[:,2] > 1) & (boxes[:,3] > 1)
    boxes,rows,scores_keep,clss = boxes[valid],rows[valid],scores_keep[valid],clss[valid]
    if not len(boxes):return []
    result=[]
    # NMS per class: overlapping damage categories can coexist.
    for cls in np.unique(clss):
        m=np.flatnonzero(clss==cls)
        if rotated:
            angles=np.rad2deg(rows[m, -1])
            candidate=[((float(boxes[j,0]),float(boxes[j,1])),
                       (float(boxes[j,2]),float(boxes[j,3])),float(angle))
                       for j,angle in zip(m,angles)]
            chosen=cv2.dnn.NMSBoxesRotated(candidate,scores_keep[m].astype(float).tolist(),conf,iou)
        else:
            candidate=[[float(boxes[j,0]-boxes[j,2]/2),float(boxes[j,1]-boxes[j,3]/2),
                        float(boxes[j,2]),float(boxes[j,3])] for j in m]
            chosen=cv2.dnn.NMSBoxes(candidate,scores_keep[m].astype(float).tolist(),conf,iou)
        for local in np.asarray(chosen).reshape(-1).astype(int):
            j=int(m[local]);cx,cy,bw,bh=boxes[j]
            angle=float(np.rad2deg(rows[j,-1])) if rotated else 0.0
            # Coordinate transformations already account for the letterbox pad.
            corners=cv2.boxPoints(((float(cx),float(cy)),(float(bw),float(bh)),angle))
            corners[:,0]=np.clip(corners[:,0],0,max(0,w-1))
            corners[:,1]=np.clip(corners[:,1],0,max(0,h-1))
            result.append({'corners':corners.astype(np.int32), 'class_id':int(cls),
                           'confidence':float(scores_keep[j]), 'area':float(bw*bh)})
    return result


def draw_detections(frame, detections, labels, colors=None):
    out=frame.copy()
    palette=colors or [(76,217,152),(255,175,54),(72,123,255),(231,75,80)]
    for item in detections:
        cls=item['class_id'];color=palette[cls%len(palette)]
        points=np.asarray(item['corners'],dtype=np.int32).reshape(4,2)
        cv2.polylines(out,[points],True,color,2,cv2.LINE_AA)
        label=labels[cls] if cls<len(labels) else str(cls)
        p=tuple(map(int,points[np.argmin(points[:,1])]))
        cv2.putText(out,label,(max(2,p[0]),max(20,p[1]-5)),
                    cv2.FONT_HERSHEY_SIMPLEX,.52,color,2,cv2.LINE_AA)
    return out


def draw_severity_hud(frame, damage_ratio, severity_level):
    """Top-right AI result card: area share and severity, never confidence."""
    out=frame.copy()
    h,w=out.shape[:2]
    if h<80 or w<200:
        return out
    ratio=max(0.0,min(1.0,float(damage_ratio or 0.0)))
    level=max(0,min(3,int(severity_level)))
    color=((51,214,128),(37,195,237),(47,178,255),(90,84,245))[level]
    names=('NONE','MILD','MODERATE','SEVERE')
    width=max(164,min(260,int(w*0.42)))
    x0=max(2,w-width-12);x1=min(w-2,x0+width)
    y0=12;y1=min(h-8,87)
    shade=out.copy()
    cv2.rectangle(shade,(x0,y0),(x1,y1),(13,30,48),-1)
    cv2.addWeighted(shade,.82,out,.18,0,out)
    cv2.rectangle(out,(x0,y0),(x1,y1),(67,105,145),1,cv2.LINE_AA)
    cv2.rectangle(out,(x0,y0),(x0+4,y1),color,-1)
    cv2.putText(out,f'DAMAGE  {ratio:.1%}',(x0+14,y0+27),
                cv2.FONT_HERSHEY_SIMPLEX,.53,(241,248,255),1,cv2.LINE_AA)
    cv2.putText(out,f'SEVERITY  {names[level]}',(x0+14,y0+56),
                cv2.FONT_HERSHEY_SIMPLEX,.48,color,1,cv2.LINE_AA)
    return out
