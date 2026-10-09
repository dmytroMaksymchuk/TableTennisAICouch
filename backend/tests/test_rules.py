from pathlib import Path

import pytest

from ttcoach.coaching.models import METRIC_UNITS, MetricValue
from ttcoach.coaching.rules import evaluate_rules, load_ruleset, violates

RULES = Path(__file__).resolve().parents[1] / "rules" / "default.yaml"


def _m(**kw):
    return {k: MetricValue(value=v, unit=METRIC_UNITS[k]) for k, v in kw.items()}


def test_default_rules_load():
    rs = load_ruleset(RULES)
    assert rs.ruleset_id == "default" and rs.ruleset_version == "2026.10.0"
    assert len(rs.rules) == 5
    assert rs.rules[0].message.startswith("Elbow angle")


def test_elbow_168_gives_feedback():
    fb, skipped = evaluate_rules(load_ruleset(RULES), "s001", "FH", _m(elbow_angle_at_contact=168.0))
    f = next(x for x in fb if x.rule_id == "fh.contact.elbow_extension")
    assert f.metric == "elbow_angle_at_contact" and f.value == 168.0
    assert f.threshold.op == "between" and (f.threshold.min, f.threshold.max) == (90.0, 150.0)
    assert f.severity == "warning" and f.phase == "contact"
    assert f.message == "Elbow angle at contact is 168°; aim for 90–150° (slight bend)."
    assert {s.rule_id for s in skipped}  # other rules lack metrics -> skipped
    assert all(s.reason == "metric_unavailable" and s.stroke_id == "s001" for s in skipped)


def test_applies_to_filters_stroke_type():
    fb, skipped = evaluate_rules(load_ruleset(RULES), "s001", "BH", _m(elbow_angle_at_contact=168.0))
    assert [f.rule_id for f in fb] == ["bh.contact.elbow_extension"]
    assert "fh.contact.elbow_extension" not in {s.rule_id for s in skipped}


def test_edited_threshold_removes_feedback(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(RULES.read_text(encoding="utf-8").replace("max: 150.0", "max: 170.0"), encoding="utf-8")
    fb, _ = evaluate_rules(load_ruleset(p), "s001", "FH", _m(elbow_angle_at_contact=168.0))
    assert fb == []


def test_typo_metric_is_load_error(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        RULES.read_text(encoding="utf-8").replace("elbow_angle_at_contact", "elbow_angel_at_contact", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="elbow_angel_at_contact") as e:
        load_ruleset(p)
    assert "elbow_angle_at_contact" in str(e.value)  # lists valid names


@pytest.mark.parametrize(
    "text,match",
    [
        ("rules: []\n", "ruleset_id"),
        ("- 1\n", "mapping"),
        ("ruleset_id: a\nruleset_version: b\nrules: [{id: x, applies_to: [FH], metric: duration_s, op: between, "
         "min: 1, severity: info, message: m}]\n", "max"),
        ("ruleset_id: a\nruleset_version: b\nrules: [{id: x, applies_to: [FH], metric: duration_s, op: lte, "
         "max: 1, severity: info, message: '{nope}'}]\n", "message"),
    ],
)
def test_invalid_files(tmp_path, text, match):
    p = tmp_path / "r.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_ruleset(p)


def test_missing_file(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        load_ruleset(tmp_path / "nope.yaml")


def test_all_ops():
    rs = load_ruleset(RULES)
    base = rs.rules[0].model_copy()
    def rule(op, lo=None, hi=None):
        return base.model_copy(update={"op": op, "min": lo, "max": hi})
    assert violates(rule("lt", hi=5), 5) and not violates(rule("lt", hi=5), 4.9)
    assert violates(rule("lte", hi=5), 5.1) and not violates(rule("lte", hi=5), 5)
    assert violates(rule("gt", lo=5), 5) and not violates(rule("gt", lo=5), 5.1)
    assert violates(rule("gte", lo=5), 4.9) and not violates(rule("gte", lo=5), 5)
    assert violates(rule("between", 1, 2), 2.1) and not violates(rule("between", 1, 2), 1.5)
    assert violates(rule("outside", 1, 2), 1.5) and not violates(rule("outside", 1, 2), 2.5)
