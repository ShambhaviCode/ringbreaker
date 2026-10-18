"""Draw a case graph with pyvis. The evidence path for the selected account is the indigo thread."""
from __future__ import annotations

import html

from pyvis.network import Network

INK = "#1A2233"
THREAD = "#2D3FA6"
TURMERIC = "#E3A33A"
STAMP = "#C8102E"
TEAL = "#3E8E85"
SLATE = "#8A94A6"
REVIEW = "#FFFFFF"

SHAPES = {"Device": "square", "IP": "triangle", "Phone": "diamond", "UPI": "dot", "Link": "triangleDown",
          "Account": "dot"}


def _style(n: dict) -> dict:
    role, label = n.get("role"), n.get("label")
    if role == "reported":
        return dict(color={"background": "#FFFFFF", "border": INK}, size=24, borderWidth=4, shape="star")
    if label == "Account":
        if n.get("status") == "frozen" or n.get("action") == "freeze":
            bg = STAMP
        elif n.get("action") == "review":
            return dict(color={"background": REVIEW, "border": STAMP}, size=18, borderWidth=4, shape="dot",
                        shapeProperties={"borderDashes": [4, 3]})
        elif role == "victim":
            bg = TEAL
        else:
            bg = TURMERIC
        size = 30 if role == "collector" else (11 if role == "victim" else 18)
        return dict(color={"background": bg, "border": INK if role == "collector" else bg}, size=size,
                    borderWidth=3 if role == "collector" else 1, shape="dot")
    if label == "UPI":
        return dict(color={"background": "#FFFFFF", "border": TURMERIC}, size=10, shape="dot", borderWidth=2)
    return dict(color={"background": SLATE, "border": SLATE}, size=13, shape=SHAPES.get(label, "dot"))


def _label(n: dict) -> str:
    if n.get("label") == "Account":
        if n.get("role") == "victim":
            return " "
        return n["id"] + ("\ncollector" if n.get("role") == "collector" else "")
    if n.get("label") in ("Device", "IP", "Phone"):
        return n["id"]
    return n.get("name") or n["id"]


def _tooltip(n: dict) -> str:
    bits = [f"{n.get('label')}: {n['id']}"]
    if n.get("holder"):
        bits.append(f"Holder: {n['holder']}")
    if n.get("name") and n["name"] != n["id"]:
        bits.append(str(n["name"]))
    if n.get("status"):
        bits.append(f"Status: {n['status']}")
    if n.get("risk") is not None:
        bits.append(f"Risk: {n['risk']}")
    if n.get("users"):
        bits.append(f"Used by {n['users']} accounts")
    if n.get("reviewer"):
        bits.append(f"In review with {n['reviewer']}")
    return html.escape("\n".join(bits))


def render(view: dict, highlight: str | None = None, height: int = 620) -> str:
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#FFFFFF", font_color=INK,
                  cdn_resources="in_line")
    path = view.get("paths", {}).get(highlight) or {}
    pids = path.get("ids", [])
    path_edges = {(pids[i], pids[i + 1]) for i in range(len(pids) - 1)} | {(pids[i + 1], pids[i])
                                                                           for i in range(len(pids) - 1)}
    for n in view.get("nodes", []):
        st = _style(n)
        if pids and n["id"] in pids:
            st["borderWidth"] = 4
            st["color"] = {**st["color"], "border": THREAD}
        net.add_node(n["id"], label=_label(n), title=_tooltip(n), font={"face": "Hind", "size": 13, "color": INK},
                     **st)
    for e in view.get("edges", []):
        on_path = (e["src"], e["dst"]) in path_edges
        if e["type"] == "PAID":
            color, width = ("#9FB3AE", 1.2)
        else:
            color, width = ("#C9CFDA", 1)
        if pids and not on_path:
            color = color + "88"
        if on_path:
            color, width = THREAD, 6
        title = f"{e['type']}" + (f"  Rs {e['amount']:,.0f}" if e.get("amount") else "")
        net.add_edge(e["src"], e["dst"], color=color, width=width, title=title,
                     arrows={"to": {"enabled": e["type"] == "PAID", "scaleFactor": 0.5}})
    net.set_options("""{
      "physics": {"barnesHut": {"gravitationalConstant": -6000, "springLength": 110, "springConstant": 0.03,
                  "avoidOverlap": 0.4}, "stabilization": {"iterations": 250}},
      "interaction": {"hover": true, "tooltipDelay": 120, "navigationButtons": false},
      "edges": {"smooth": {"type": "continuous"}}
    }""")
    out = net.generate_html()
    fit = ("<script>(function(){function f(){try{network.fit({animation:false});}catch(e){}}"
           "var t=setInterval(function(){if(typeof network!=='undefined'&&network){clearInterval(t);"
           "network.once('stabilizationIterationsDone',function(){f();network.setOptions({physics:false});});"
           "setTimeout(f,1200);"
           "try{new ResizeObserver(function(){if(document.body.clientWidth>0){network.redraw();f();}})"
           ".observe(document.body);}catch(e){}}},100);})();</script>"
           "<style>body{margin:0}.card{border:0!important}#mynetwork{border:0!important;float:none!important}</style>")
    out = out.replace("</body>", fit + "</body>", 1)
    return out.replace("<head>", "<head><link href='https://fonts.googleapis.com/css2?family=Hind:wght@400;600"
                                 "&display=swap' rel='stylesheet'>", 1)


LEGEND = [
    ("Reported by the customer", "#FFFFFF", INK, "star"),
    ("Ring account", TURMERIC, TURMERIC, "dot"),
    ("Frozen", STAMP, STAMP, "dot"),
    ("Waiting for an analyst", REVIEW, STAMP, "dot"),
    ("Customer who paid the ring", TEAL, TEAL, "dot"),
    ("Shared device, IP or phone", SLATE, SLATE, "square"),
    ("Evidence path", THREAD, THREAD, "line"),
]


def legend_html() -> str:
    items = []
    for text, bg, border, shape in LEGEND:
        if shape == "line":
            mark = f"<span style='display:inline-block;width:22px;height:4px;background:{bg};border-radius:2px'></span>"
        else:
            radius = "50%" if shape == "dot" else ("2px" if shape == "square" else "50%")
            star = "&#9733;" if shape == "star" else ""
            mark = (f"<span style='display:inline-flex;align-items:center;justify-content:center;width:12px;height:12px;"
                    f"background:{bg};border:2px solid {border};border-radius:{radius};font-size:9px'>{star}</span>")
        items.append(f"<span class='rb-legend-item'>{mark}<span>{text}</span></span>")
    return "<div class='rb-legend'>" + "".join(items) + "</div>"
