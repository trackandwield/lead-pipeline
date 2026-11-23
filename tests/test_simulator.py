import random
from statistics import mean

from pipeline.simulator import DRIFT_SOURCE, generate, sample_outcome


def test_same_seed_same_leads():
    a, b = generate(n=50, seed=3), generate(n=50, seed=3)
    assert [x.visible() for x in a] == [y.visible() for y in b]


def test_count_weeks_and_fictional_domains():
    leads = generate(n=120, weeks=12)
    assert len(leads) == 120
    assert {lead.week for lead in leads} == set(range(12))
    # RFC 2606 reserves example.com and its subdomains; HubSpot rejects a bare .example TLD.
    assert all(lead.email.endswith(".example.com") for lead in leads)
    assert all(lead.domain.endswith(".example.com") for lead in leads)


def test_hidden_truth_is_kept_out_of_visible_fields():
    lead = generate(n=1)[0]
    assert "true_quality" in lead.hidden
    assert "hidden" not in lead.visible()
    assert "true_quality" not in str(lead.visible())


def test_no_drift_without_switch():
    assert not any(lead.source == DRIFT_SOURCE for lead in generate(n=200))


def test_drift_starts_on_its_week_and_converts_worse():
    leads = generate(n=400, weeks=12, drift_week=6)
    drifted = [lead for lead in leads if lead.hidden["drifted"]]
    normal = [lead for lead in leads if lead.week >= 6 and not lead.hidden["drifted"]]
    assert drifted and all(lead.week >= 6 for lead in drifted)
    assert all(lead.source == DRIFT_SOURCE for lead in drifted)
    assert mean(lead.hidden["true_quality"] for lead in drifted) < \
        mean(lead.hidden["true_quality"] for lead in normal) - 0.15


def test_better_leads_become_sql_more_often():
    leads = generate(n=600, seed=11)
    rng = random.Random(0)
    outcomes = {o.lead_id: o for o in (sample_outcome(lead, rng) for lead in leads)}
    ranked = sorted(leads, key=lambda lead: lead.hidden["true_quality"])
    bottom, top = ranked[:150], ranked[-150:]

    def rate(group):
        return mean(outcomes[lead.lead_id].became_sql for lead in group)

    assert rate(top) > rate(bottom) + 0.3


def test_won_only_after_sql():
    rng = random.Random(1)
    for lead in generate(n=100):
        o = sample_outcome(lead, rng)
        if not o.became_sql:
            assert o.won is None and o.closed_after_days is None
        else:
            assert o.closed_after_days > o.sql_after_days
