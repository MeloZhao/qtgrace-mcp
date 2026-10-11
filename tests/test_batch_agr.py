"""Tests for the .agr generator — pure Python, no QtGrace needed."""

import pytest

from qtgrace_mcp.agr import build_agr


def minimal(**graph):
    g = {"sets": [{"data": [[0, 0], [1, 1]]}]}
    g.update(graph)
    return {"graphs": [g]}


# --- structure ------------------------------------------------------------

def test_header_and_version():
    text = build_agr(minimal())
    assert text.startswith("# Grace project file")
    assert "@version 50121" in text


def test_no_inline_comments():
    """This QtGrace build rejects inline '#', so the generator must never emit one."""
    text = build_agr(minimal(title="a # b"))
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # a '#' is only allowed inside a quoted string
        assert line.count('"') >= 2 or "#" not in line, f"inline comment leaked: {line!r}"


def test_hash_inside_quoted_title_is_kept():
    """A '#' in a label is data, not a comment — it must not truncate the title."""
    text = build_agr(minimal(title="rate #1"))
    assert '@    title "rate #1"' in text


def test_header_comment_is_preserved():
    """The project header must survive comment stripping."""
    text = build_agr(minimal())
    assert text.splitlines()[0] == "# Grace project file"


def test_graph_is_switched_on():
    """Without '@gN on' only g0 renders."""
    text = build_agr({"graphs": [
        {"sets": [{"data": [[0, 0]]}]},
        {"sets": [{"data": [[0, 1]]}]},
    ]})
    assert "@g0 on" in text
    assert "@g1 on" in text
    assert "@g1 hidden false" in text


def test_data_block_terminated():
    text = build_agr(minimal())
    assert "@target g0.s0" in text
    assert "\n&\n" in text


# --- graph types ----------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("XY", "XY"), ("xy", "XY"),
    ("CHART", "Chart"), ("chart", "Chart"),
    ("POLAR", "Polar"), ("POLARV2", "PolarV2"), ("polar_v2", "PolarV2"),
    ("PIE", "Pie"),
])
def test_graph_type_names(given, expected):
    text = build_agr(minimal(graph_type=given))
    assert f"@g0 type {expected}" in text


def test_unknown_graph_type_raises():
    with pytest.raises(ValueError) as e:
        build_agr(minimal(graph_type="NOTATYPE"))
    assert "NOTATYPE" in str(e.value)


def test_chart_emits_bar_gap():
    text = build_agr(minimal(graph_type="CHART"))
    assert "@g0 bar hgap" in text


# --- axis scaling ---------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("log", "Logarithmic"), ("Logarithmic", "Logarithmic"),
    ("normal", "Normal"), ("linear", "Normal"),
    ("reciprocal", "Reciprocal"), ("logit", "Logit"),
])
def test_axis_scale(given, expected):
    text = build_agr(minimal(xaxis={"scale": given}))
    assert f"@    xaxes scale {expected}" in text


def test_unknown_scale_raises():
    with pytest.raises(ValueError):
        build_agr(minimal(xaxis={"scale": "banana"}))


# --- bar handling ---------------------------------------------------------

def test_bar_set_gets_no_connecting_line():
    """Bar tops must not be joined by a line."""
    text = build_agr({"graphs": [{"graph_type": "CHART",
                                  "sets": [{"type": "bar", "data": [[1, 2]]}]}]})
    assert "@    s0 line type 0" in text


def test_xy_set_keeps_line():
    text = build_agr(minimal())
    assert "@    s0 line type 1" in text


def test_bar_fill_defaults_to_line_colour():
    text = build_agr({"graphs": [{"graph_type": "CHART",
                                  "sets": [{"type": "bar", "lineColor": 4,
                                            "data": [[1, 2]]}]}]})
    assert "@    s0 symbol fill color 4" in text


# --- data validation ------------------------------------------------------

def test_wrong_column_count_is_reported():
    with pytest.raises(ValueError) as e:
        build_agr({"graphs": [{"sets": [{"type": "xydy", "data": [[1, 2]]}]}]})
    msg = str(e.value)
    assert "xydy" in msg and "3 numbers" in msg and "row 0" in msg


def test_empty_data_is_reported():
    with pytest.raises(ValueError) as e:
        build_agr({"graphs": [{"sets": [{"data": []}]}]})
    assert "no data rows" in str(e.value)


def test_unknown_set_type_is_reported():
    with pytest.raises(ValueError) as e:
        build_agr({"graphs": [{"sets": [{"type": "bogus", "data": [[1, 2]]}]}]})
    assert "unknown set type" in str(e.value)


def test_non_numeric_value_is_reported():
    with pytest.raises(ValueError) as e:
        build_agr({"graphs": [{"sets": [{"data": [["a", "b"]]}]}]})
    assert "not a number" in str(e.value)


def test_nan_is_reported():
    with pytest.raises(ValueError) as e:
        build_agr({"graphs": [{"sets": [{"data": [[float("nan"), 1]]}]}]})
    assert "not finite" in str(e.value)


def test_correct_xydy_accepted():
    text = build_agr({"graphs": [{"sets": [{"type": "xydy", "data": [[1, 2, 0.1]]}]}]})
    assert "1 2 0.1" in text


# --- quoting / special characters ----------------------------------------

def test_quotes_are_stripped_from_labels():
    text = build_agr(minimal(title='say "hi"'))
    assert '@    title "say hi"' in text


def test_unicode_title_survives():
    text = build_agr(minimal(title="中文标题"))
    assert "中文标题" in text


# --- advanced features (all verified against real QtGrace output) ---------

def test_fill_emits_type_and_colour():
    text = build_agr({"graphs": [{"sets": [
        {"type": "xy", "fill": {"type": 2, "color": 7}, "data": [[0, 0], [1, 1]]}]}]})
    assert "@    s0 fill type 2" in text
    assert "@    s0 fill color 7" in text


def test_fill_absent_emits_type_0():
    text = build_agr(minimal())
    assert "@    s0 fill type 0" in text


def test_baseline_and_dropline():
    text = build_agr({"graphs": [{"sets": [
        {"type": "xy", "baseline": {"type": 1}, "dropline": True, "data": [[0, 0]]}]}]})
    assert "@    s0 baseline type 1" in text
    assert "@    s0 baseline on" in text
    assert "@    s0 dropline on" in text


def test_avalue_labels():
    text = build_agr({"graphs": [{"sets": [
        {"type": "xy", "avalue": {"type": 2, "prec": 2, "offset": [0, 0.1]},
         "data": [[0, 1]]}]}]})
    assert "@    s0 avalue on" in text
    assert "@    s0 avalue prec 2" in text
    assert "@    s0 avalue offset 0, 0.1" in text


def test_avalue_absent_is_off():
    assert "@    s0 avalue off" in build_agr(minimal())


def test_zero_axis_uses_type_zero_true():
    """`zeroxaxis tick on` alone draws nothing — `xaxis type zero true` is the switch."""
    text = build_agr(minimal(xaxis={"zero": True}))
    assert "@    xaxis type zero true" in text
    assert "@    zeroxaxis tick on" in text


def test_alt_axis():
    text = build_agr(minimal(xaxis={"alt": True}, yaxis={"alt": True}))
    assert "@    altxaxis tick on" in text
    assert "@    altyaxis tick on" in text


def test_text_annotation():
    text = build_agr({"graphs": [{"sets": [{"data": [[0, 0]]}]}],
                      "annotations": [{"type": "text", "x": 0.1, "y": 0.9,
                                       "text": "hello", "graph": 0}]})
    assert "@with string" in text
    assert "@    string loctype world" in text
    assert "@    string 0.1, 0.9" in text
    assert '@    string def "hello"' in text


def test_line_annotation_is_committed():
    """Without the top-level `@line def` the line parses but never draws."""
    text = build_agr({"graphs": [{"sets": [{"data": [[0, 0]]}]}],
                      "annotations": [{"type": "arrow", "x1": 0.2, "y1": 0.8,
                                       "x2": 0.8, "y2": 0.2, "graph": 0}]})
    assert "@with line" in text
    assert "@    line 0.2, 0.8, 0.8, 0.2" in text
    assert "@    line arrow 1" in text
    assert "\n@line def\n" in text


def test_plain_line_has_arrow_0():
    text = build_agr({"graphs": [{"sets": [{"data": [[0, 0]]}]}],
                      "annotations": [{"type": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]})
    assert "@    line arrow 0" in text


def test_unknown_annotation_type_raises():
    with pytest.raises(ValueError) as e:
        build_agr({"graphs": [{"sets": [{"data": [[0, 0]]}]}],
                   "annotations": [{"type": "hexagon"}]})
    assert "hexagon" in str(e.value)


def test_timestamp():
    text = build_agr({"graphs": [{"sets": [{"data": [[0, 0]]}]}],
                      "timestamp": {"format": "run 42"}})
    assert "@timestamp on" in text
    assert '@timestamp def "run 42"' in text


def test_advanced_and_silent_traps_sections():
    from qtgrace_mcp import catalog
    assert len(catalog.get_section("advanced")["advanced"]) >= 8
    assert len(catalog.get_section("silent_traps")["silent_traps"]) >= 6


# --- full coverage: the remaining directives -----------------------------

def test_frame():
    text = build_agr(minimal(frame={"type": 1, "color": 8, "linewidth": 2}))
    assert "@    frame type 1" in text
    assert "@    frame color 8" in text


def test_autoscale():
    assert "@g0 autoscale type AUTO" in build_agr(minimal(autoscale="auto"))
    assert "@g0 autoscale type NONE" in build_agr(minimal(autoscale="none"))


def test_fixedpoint():
    text = build_agr(minimal(fixedpoint={"type": 0, "prec": [3, 4]}))
    assert "@g0 fixedpoint on" in text
    assert "@g0 fixedpoint prec 3, 4" in text


def test_world_stack_uses_different_order():
    """stack world is (xmin, xmax, ymin, ymax) — NOT the world order."""
    text = build_agr(minimal(worldStack=[[1970, 1990, 320, 350]]))
    assert "@    stack world 1970, 1990, 320, 350" in text


def test_znorm():
    assert "@    znorm 1" in build_agr(minimal(znorm=1))


def test_legend_box():
    text = build_agr(minimal(legend={"pos": [0.5, 0.5],
                                     "box": {"color": 8, "linewidth": 2, "fillColor": 7}}))
    assert "@    legend box color 8" in text
    assert "@    legend box linewidth 2" in text
    assert "@    legend box fill color 7" in text


def test_set_comment_and_symbol_skip():
    text = build_agr({"graphs": [{"sets": [
        {"type": "xy", "comment": "raw data", "symbolSkip": 2, "symbolChar": 65,
         "data": [[0, 0]]}]}]})
    assert '@    s0 comment "raw data"' in text
    assert "@    s0 symbol skip 2" in text
    assert "@    s0 symbol char 65" in text


def test_errorbar_extras():
    text = build_agr({"graphs": [{"sets": [
        {"type": "xydy", "errorbarType": "up", "errorbarLength": 1.5,
         "data": [[0, 1, 0.1]]}]}]})
    assert "@    s0 errorbar type up" in text
    assert "@    s0 errorbar length 1.5" in text


def test_ellipse_and_box_annotations():
    text = build_agr({"graphs": [{"sets": [{"data": [[0, 0]]}]}],
                      "annotations": [{"type": "ellipse", "x1": 0, "y1": 0, "x2": 1, "y2": 1},
                                      {"type": "box", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]})
    assert "@with ellipse" in text and "\n@ellipse def\n" in text
    assert "@with box" in text and "\n@box def\n" in text


def test_description_defaults_and_date():
    text = build_agr({"description": "my project", "defaults": {"linewidth": 1.2},
                      "date": {"wrap": True, "wrapYear": 1950},
                      "graphs": [{"sets": [{"data": [[0, 0]]}]}]})
    assert '@description "my project"' in text
    assert "@default linewidth 1.2" in text
    assert "@date wrap on" in text
    assert "@date wrap year 1950" in text
