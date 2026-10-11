"""Reviewer findings: spoken decimals, callback corrections, policy zero values."""

from fb.nodecode.embed import namespace

NS = namespace()


# ---- 1. spoken decimals: the words after 'point' are a digit sequence
def test_point_reads_digits():
    pa = NS["parse_amount"]
    assert pa("two hundred point zero five") == 200.05
    assert pa("two hundred point five") == 200.5
    assert pa("two hundred point two five") == 200.25
    assert pa("two hundred point twenty five") == 200.25
    assert pa("four hundred and twenty point five zero") == 420.50
    assert pa("200 point 05") == 200.05
    assert pa("two hundred point five pounds") == 200.5


def test_point_keeps_other_amounts():
    pa = NS["parse_amount"]
    assert pa("two hundred pounds and five pence") == 200.05
    assert pa("four hundred and twenty pounds fifty") == 420.50
    assert pa("six hundred and thirty-seven fifty") == 637.50
    assert pa("at this point I can pay 200") == 200.0
