"""Short display names must stay unique across a set of funds: the Portfolio overlap heatmap
crashed on two UTI Nifty 50 variants that both shortened to 'UTI Nifty 50 Index Fund'."""

from mutual_funds.display import short_scheme_name, unique_short_names


def test_short_name_drops_the_disambiguating_code_suffix():
    assert short_scheme_name("Motilal Oswal Midcap Fund - Growth (127042)") == "Motilal Oswal Midcap Fund"


def test_unique_short_names_keep_distinct_variants_apart():
    names = [
        "UTI Nifty 50 Index Fund - Direct Plan - Growth",
        "UTI Nifty 50 Index Fund - Direct Plan - IDCW",
        "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
    ]
    out = unique_short_names(names)
    assert out["Parag Parikh Flexi Cap Fund - Direct Plan - Growth"] == "Parag Parikh Flexi Cap Fund"
    assert out["UTI Nifty 50 Index Fund - Direct Plan - Growth"] == "UTI Nifty 50 Index Fund (Growth)"
    assert out["UTI Nifty 50 Index Fund - Direct Plan - IDCW"] == "UTI Nifty 50 Index Fund (IDCW)"
    assert len(set(out.values())) == 3


def test_unique_short_names_fall_back_to_the_full_name_when_option_does_not_separate():
    names = ["Motilal Oswal Midcap Fund - Growth (127042)", "Motilal Oswal Midcap Fund - Growth (127039)"]
    out = unique_short_names(names)
    assert len(set(out.values())) == 2
    assert all(v.startswith("Motilal Oswal Midcap Fund") for v in out.values())
