#!/usr/bin/env python3
"""
skin_profile.py - Biểu diễn 'màu da chuẩn' của một nhân vật thành hồ sơ tái dùng,
và đưa các ảnh khác về cùng base da đó.

  extract  : ảnh gốc  -> skin_profile.json (+ swatch.png)
  apply    : ảnh khác + profile -> ảnh đã đồng nhất base da

Vì sao không dùng 1 mã hex: da là một PHÂN PHỐI, và hex trộn lẫn độ sáng (đổi theo
đèn từng cảnh) với hue da (cần giữ cố định). Profile tách 2 thứ đó ra:
  - hue/chroma da (a,b trong LAB)  -> ĐẶC TRƯNG nhân vật, giữ cố định
  - độ sáng (L)                    -> chuẩn hoá được, tuỳ chọn khớp hay giữ
  - palette hex 3 tầng             -> cho người & AI prompt đọc hiểu
"""
import cv2, numpy as np, json, sys, argparse

def skin_pixels_mask(bgr):
    y = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    Cr, Cb = y[..., 1].astype(int), y[..., 2].astype(int)
    s = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 1]
    return (Cr >= 133) & (Cr <= 173) & (Cb >= 77) & (Cb <= 127) & (s < 150)

def _hex(bgr_px): return "#%02X%02X%02X" % (int(bgr_px[2]), int(bgr_px[1]), int(bgr_px[0]))

def extract_profile(path, name="character"):
    bgr = cv2.imread(path)
    m = skin_pixels_mask(bgr)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, a, b = lab[..., 0][m], lab[..., 1][m], lab[..., 2][m]
    bgr_px = bgr[m].astype(np.float32)
    # palette 3 tầng theo độ sáng L
    def tone(pct):
        lo, hi = np.percentile(L, pct - 10), np.percentile(L, pct + 10)
        sel = (L >= lo) & (L <= hi)
        return _hex(bgr_px[sel].mean(0))
    # mô tả cho người / AI prompt
    hue = "ấm (warm)" if b.mean() > a.mean() - 2 else "trung tính"
    light = "sáng" if L.mean() > 150 else "trung bình" if L.mean() > 110 else "trầm"
    return {
        "character": name,
        "color_space": "LAB OpenCV (0-255)",
        "skin": {
            "palette_hex": {"shadow": tone(25), "base": tone(50), "highlight": tone(80)},
            "chroma": {"a_mean": round(float(a.mean()), 1), "a_std": round(float(a.std()), 1),
                       "b_mean": round(float(b.mean()), 1), "b_std": round(float(b.std()), 1)},
            "lightness": {"L_mean": round(float(L.mean()), 1), "L_std": round(float(L.std()), 1)},
            "desc": f"da {light}, tông {hue}",
        },
        "_stats_for_match": [float(L.mean()), float(L.std()),
                             float(a.mean()), float(a.std()),
                             float(b.mean()), float(b.std())],
    }

def make_swatch(profile, out="swatch.png"):
    pal = profile["skin"]["palette_hex"]; W = 360
    img = np.zeros((140, W, 3), np.uint8)
    for i, k in enumerate(["shadow", "base", "highlight"]):
        h = pal[k].lstrip("#"); r, g, bl = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        img[:110, i*W//3:(i+1)*W//3] = (bl, g, r)
        cv2.putText(img, pal[k], (i*W//3+6, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    cv2.imwrite(out, img)

def apply_profile(path, profile, out, match_lightness=True):
    """Đưa ảnh về base da của profile (Reinhard). match_lightness=False để giữ
    sáng-tối gốc của cảnh, chỉ đồng nhất hue da."""
    bgr = cv2.imread(path)
    T = profile["_stats_for_match"]                       # [Lm,Ls,am,as,bm,bs]
    m = skin_pixels_mask(bgr)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    src = [lab[..., 0][m].mean(), lab[..., 0][m].std(),
           lab[..., 1][m].mean(), lab[..., 1][m].std(),
           lab[..., 2][m].mean(), lab[..., 2][m].std()]
    def xf(ch, sm, ss, tm, ts):
        return (lab[..., ch] - sm) * np.clip(ts / max(ss, 1e-3), 0.5, 2.0) + tm
    if match_lightness:
        lab[..., 0] = xf(0, src[0], src[1], T[0], T[1])
    lab[..., 1] = xf(1, src[2], src[3], T[2], T[3])
    lab[..., 2] = xf(2, src[4], src[5], T[4], T[5])
    res = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    cv2.imwrite(out, res)

# ---------------------------------------------------------------- VIDEO
def _global_skin_stats(path, n=20):
    import numpy as np
    cap = cv2.VideoCapture(path); tot = int(cap.get(7)) or 1; rows = []
    for i in np.linspace(0, tot - 1, n).astype(int):
        cap.set(1, int(i)); ok, f = cap.read()
        if not ok: continue
        m = skin_pixels_mask(f); lab = cv2.cvtColor(f, cv2.COLOR_BGR2LAB).astype(np.float32)
        if m.sum() < 500: continue
        rows.append([lab[..., 0][m].mean(), lab[..., 0][m].std(),
                     lab[..., 1][m].mean(), lab[..., 1][m].std(),
                     lab[..., 2][m].mean(), lab[..., 2][m].std()])
    cap.release()
    if not rows:
        return None, 0.0
    rows = np.array(rows)
    drift = abs(rows[-3:, 0].mean() - rows[:3, 0].mean()) + abs(rows[-3:, 2].mean() - rows[:3, 2].mean())
    return rows.mean(0), drift

def apply_video(inp, profile, out, match_lightness=True, deplastic=True,
                cas=0.4, grain=6, crf=18):
    import subprocess, numpy as np
    T = profile["_stats_for_match"]
    gstats, drift = _global_skin_stats(inp)
    perframe = drift > 6.0
    cap = cv2.VideoCapture(inp); fps = cap.get(5) or 24
    W = int(cap.get(3)); H = int(cap.get(4))
    vf = []
    if deplastic:
        if cas > 0:   vf.append(f"cas=strength={cas:.2f}")
        if grain > 0: vf.append(f"noise=alls={int(grain)}:allf=t+u")
    vf = ",".join(vf) if vf else "null"
    p = subprocess.Popen(["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-vf", vf, "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-crf", str(crf), out, "-loglevel", "error"], stdin=subprocess.PIPE)
    def xf(lab, ch, sm, ss, tm, ts):
        return (lab[..., ch] - sm) * np.clip(ts / max(ss, 1e-3), 0.5, 2.0) + tm
    ema = None
    while True:
        ok, f = cap.read()
        if not ok: break
        if perframe:
            m = skin_pixels_mask(f); lab0 = cv2.cvtColor(f, cv2.COLOR_BGR2LAB).astype(np.float32)
            if m.sum() > 500:
                s = np.array([lab0[..., 0][m].mean(), lab0[..., 0][m].std(),
                              lab0[..., 1][m].mean(), lab0[..., 1][m].std(),
                              lab0[..., 2][m].mean(), lab0[..., 2][m].std()])
                ema = s if ema is None else 0.9 * ema + 0.1 * s
            src = ema
        else:
            src = gstats
        if src is not None:
            lab = cv2.cvtColor(f, cv2.COLOR_BGR2LAB).astype(np.float32)
            if match_lightness: lab[..., 0] = xf(lab, 0, src[0], src[1], T[0], T[1])
            lab[..., 1] = xf(lab, 1, src[2], src[3], T[2], T[3])
            lab[..., 2] = xf(lab, 2, src[4], src[5], T[4], T[5])
            f = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
        p.stdin.write(f.tobytes())
    cap.release(); p.stdin.close(); p.wait()
    return dict(drift=round(float(drift), 1), mode="per-frame" if perframe else "global")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract"); e.add_argument("image"); e.add_argument("-n", "--name", default="character"); e.add_argument("-o", "--out", default="skin_profile.json")
    a = sub.add_parser("apply"); a.add_argument("image"); a.add_argument("profile"); a.add_argument("-o", "--out", default="aligned.png"); a.add_argument("--keep-light", action="store_true")
    v = sub.add_parser("apply-video"); v.add_argument("video"); v.add_argument("profile"); v.add_argument("-o", "--out", default="aligned.mp4")
    v.add_argument("--keep-light", action="store_true"); v.add_argument("--no-deplastic", action="store_true")
    args = ap.parse_args()
    if args.cmd == "extract":
        prof = extract_profile(args.image, args.name)
        json.dump(prof, open(args.out, "w"), ensure_ascii=False, indent=2)
        make_swatch(prof, args.out.replace(".json", "_swatch.png"))
        print(json.dumps(prof["skin"], ensure_ascii=False, indent=2)); print("->", args.out)
    elif args.cmd == "apply":
        prof = json.load(open(args.profile))
        apply_profile(args.image, prof, args.out, match_lightness=not args.keep_light)
        print("->", args.out)
    else:
        prof = json.load(open(args.profile))
        info = apply_video(args.video, prof, args.out,
                           match_lightness=not args.keep_light, deplastic=not args.no_deplastic)
        print(f"[{info['mode']}] drift={info['drift']} -> {args.out}")
