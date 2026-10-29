"""Ordre de lecture (XY-cut récursif, N colonnes), images flottantes, colonnes réelles vs tableau de mise en page."""
from .model import Cols


def _span(it, axis):
    return (it.x0, it.x1) if axis == "x" else (it.y0, it.y1)


def _split(items, axis, gap):
    order = sorted(items, key=lambda it: _span(it, axis)[0])
    groups = [[order[0]]]
    end = _span(order[0], axis)[1]
    for it in order[1:]:
        a, b = _span(it, axis)
        if a - end > gap:
            groups.append([it])
            end = b
        else:
            groups[-1].append(it)
            end = max(end, b)
    return groups


def _bounds(items):
    return (min(i.x0 for i in items), min(i.y0 for i in items),
            max(i.x1 for i in items), max(i.y1 for i in items))


def _side_by_side(groups):
    """Les groupes ont-ils une extension verticale commune (sinon ce n'est pas une mise en colonnes) ?"""
    bs = [_bounds(g) for g in groups]
    for a, b in zip(bs, bs[1:]):
        ov = min(a[3], b[3]) - max(a[1], b[1])
        if ov < 0.5 * min(a[3] - a[1], b[3] - b[1]):
            return False
    return True


def xy_cut(items, cfg):
    if len(items) <= 1:
        return list(items)
    gx = _split(items, "x", cfg.col_gap)
    if len(gx) > 1 and _side_by_side(gx):
        groups = [xy_cut(g, cfg) for g in gx]
        return [Cols(groups, [_bounds(g) for g in gx])]
    gy = _split(items, "y", cfg.row_gap)
    if len(gy) > 1:
        out = []
        for g in gy:
            out += xy_cut(g, cfg)
        return out
    return sorted(items, key=lambda it: (it.y0, it.x0))


def _text_lines(nodes):
    n = 0
    for x in nodes:
        if x.kind == "para":
            n += x.n
        elif x.kind == "cols":
            n += sum(_text_lines(g) for g in x.groups)
        elif x.kind == "box":
            n += sum(c.n for c in x.children if c.kind == "para")
    return n


def mark_real_columns(nodes, cfg, top_level=True):
    """Au niveau de la page : de vraies colonnes de texte deviennent des sections Word à N colonnes."""
    for n in nodes:
        if n.kind != "cols":
            continue
        n.real = top_level and all(_text_lines(g) >= cfg.min_col_lines for g in n.groups) \
            and all(all(x.kind != "cols" for x in g) for g in n.groups)
        for g in n.groups:
            mark_real_columns(g, cfg, top_level=False)


# ---------------------------------------------------------------- images flottantes
def decide_floats(items, cfg):
    """Une image avec du texte à côté ET du texte large au-dessus/dessous est flottante (habillage)."""
    paras = [i for i in items if i.kind == "para"]
    floats = []
    for im in items:
        if im.kind != "img" or im.formula or im.w < 20 or im.h < 20:
            continue
        beside = []
        for p in paras:
            ov = min(p.y1, im.y1) - max(p.y0, im.y0)
            if ov < 0.4 * min(p.h, im.h) or p.n < 2:
                continue
            right = p.x0 >= im.x1 - 1 and p.x0 - im.x1 <= cfg.float_gap
            left = p.x1 <= im.x0 + 1 and im.x0 - p.x1 <= cfg.float_gap
            if right or left:
                beside.append((p, "R" if right else "L"))
        if not beside:
            continue
        beside.sort(key=lambda b: b[0].y0)
        host = beside[0][0]
        im.floating = True
        im.host = host
        host.floats.append(im)
        for p, side in beside:
            p.near_float = True
        floats.append(im)
    return floats


def order_page(pm, cfg):
    floats = decide_floats(pm.items, cfg)
    flow = [i for i in pm.items if i not in floats]
    for it in flow:
        if it.kind == "box":
            it.children = xy_cut(it.children, cfg) if it.children else []
    nodes = xy_cut(flow, cfg) if flow else []
    mark_real_columns(nodes, cfg)
    pm.nodes = nodes
    pm.stats["floats"] = len(floats)
    pm.stats["cols_real"] = sum(1 for n in nodes if n.kind == "cols" and n.real)
    pm.stats["cols_table"] = sum(1 for n in nodes if n.kind == "cols" and not n.real)
    return pm
