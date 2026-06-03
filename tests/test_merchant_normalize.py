"""Tests for the dynamic merchant normalization system (no network).

Two layers:
  - core.merchant_normalize: universal structural cleaning (no brand/city lists).
  - core.merchant_cluster:   data-driven variant collapsing.
Cases are drawn from real transaction descriptors.
"""
import pytest
from core.merchant_normalize import normalize_merchant, looks_low_confidence
from core.merchant_cluster import build_canonical_map


class TestBrandPreserved:
    """The old pipeline deleted 8+ letter words; brands must now survive."""

    @pytest.mark.parametrize("raw, expected", [
        ("CHIPOTLE MEXICAN GRILL", "Chipotle Mexican Grill"),  # was "Mexican Grill"
        ("ENTERTAINMENT EXPERTS", "Entertainment Experts"),    # was "Experts"
        ("BRASSERIE LIBERTE", "Brasserie Liberte"),            # was "Liberte"
        ("PROMETRIC LLC", "Prometric"),                        # corp suffix trimmed
        ("DELOITTE & TOUCH", "Deloitte & Touch"),              # was "Touch"
        ("MICHAELS STORES 8808 STERLING VA 8808005310", "Michaels"),  # was "Stores"
        ("LA CAMPESINA RESTAURANT", "La Campesina"),           # was "La"
    ])
    def test_brand_survives(self, raw, expected):
        assert normalize_merchant(raw) == expected


class TestNoiseStripping:
    @pytest.mark.parametrize("raw, expected", [
        ("AMC 9640 ONLINE", "AMC"),
        ("TARGET 00010884091", "Target"),
        ("TST*DISTRICT CHICKEN A VIENNA VA 0017843", "District Chicken"),
    ])
    def test_trailing_noise_removed(self, raw, expected):
        assert normalize_merchant(raw) == expected

    @pytest.mark.parametrize("raw, expected", [
        ("7-Eleven", "7 Eleven"),                              # leading digit kept
        ("7 BREW COFFEE SB294 CHRISTIANSBURVA", "7 Brew Coffee"),
    ])
    def test_leading_number_kept(self, raw, expected):
        assert normalize_merchant(raw) == expected

    def test_state_code_only_cuts_when_trailing(self):
        # "AL" mid-name is NOT Alabama; the brand survives.
        out = normalize_merchant("TST*DC AL TOQUE WASHINGTON DC 00293750032195644268AA")
        assert out.startswith("DC Al Toque")


class TestApostrophes:
    @pytest.mark.parametrize("raw, expected", [
        ("TST* LEI'D POKE", "Lei'd Poke"),
        ("Jersey Mike's", "Jersey Mike's"),
        ("Trader Joe's", "Trader Joe's"),
    ])
    def test_apostrophe_intact(self, raw, expected):
        assert normalize_merchant(raw) == expected


class TestAcronymsAndCase:
    @pytest.mark.parametrize("raw, expected", [
        ("CVS", "CVS"),
        ("DISCOVER", "Discover"),
        ("CLOUDFLARE", "Cloudflare"),
        ("Shake Shack", "Shake Shack"),
    ])
    def test_casing(self, raw, expected):
        assert normalize_merchant(raw) == expected


class TestPeerPayments:
    @pytest.mark.parametrize("raw, expected", [
        ('Joseph Dooley "Tea"', "Joseph Dooley"),
        ('lesbian glen "🏒"', "Lesbian Glen"),
        ('Tommy Le "Italy - Spring 2026 group split"', "Tommy Le"),
    ])
    def test_person_extracted(self, raw, expected):
        assert normalize_merchant(raw) == expected


class TestEdgeCases:
    def test_empty(self):
        assert normalize_merchant("") == ""
        assert normalize_merchant("   ") == ""

    def test_none_safe(self):
        assert normalize_merchant(None) == ""


class TestLowConfidenceGate:
    @pytest.mark.parametrize("raw", [
        "HUNGER STATION REST LISB RUA ATALAIA 1PR 8.00",
        "SPO*BARCELONARESTONBWB",
        "******.*************",
    ])
    def test_flagged(self, raw):
        assert looks_low_confidence(raw, normalize_merchant(raw)) is True

    @pytest.mark.parametrize("raw", [
        "CHIPOTLE MEXICAN GRILL",
        "AMC 9640 ONLINE",
        "Shake Shack",
    ])
    def test_clean_not_flagged(self, raw):
        assert looks_low_confidence(raw, normalize_merchant(raw)) is False


class TestClustering:
    """Variant collapsing with no city or brand list — learned from the data."""

    def test_collapses_location_variants(self):
        raws = [
            "HEYTEA TYSONS CORNER",
            "HEYTEA-US- TYSONS MCLEAN VA APPLE PAY ENDING IN 9799",
            "HEYTEA TYSONS",
        ]
        cmap = build_canonical_map(raws)
        assert set(cmap.values()) == {"Heytea"}

    def test_keeps_distinct_brands_sharing_first_word(self):
        raws = [
            "AMERICAN AIRLINES 123", "AMERICAN AIRLINES 456",
            "AMERICAN EAGLE 789", "AMERICAN EAGLE 012",
        ]
        cmap = build_canonical_map(raws)
        assert cmap["AMERICAN AIRLINES 123"] == "American Airlines"
        assert cmap["AMERICAN EAGLE 789"] == "American Eagle"

    def test_does_not_collapse_to_two_letter_token(self):
        raws = [
            "TST*DC AL TOQUE WASHINGTON DC 001",
            "TST*DC AL TOQUE WASHINGTON DC 002",
            "DC BRAU BREWING CO",
        ]
        cmap = build_canonical_map(raws)
        assert cmap["TST*DC AL TOQUE WASHINGTON DC 001"] != "DC"
        # the two Al Toque rows share a canonical; Brau is different
        assert cmap["TST*DC AL TOQUE WASHINGTON DC 001"] == cmap["TST*DC AL TOQUE WASHINGTON DC 002"]
        assert cmap["DC BRAU BREWING CO"] != cmap["TST*DC AL TOQUE WASHINGTON DC 001"]

    def test_generic_prefix_not_collapsed(self):
        # "Payment to Apple" must not be flattened into "Payment To".
        cmap = build_canonical_map([
            "Payment to Apple Services", "Payment to Airgsm Pte. Ltd.",
        ])
        assert "Apple" in cmap["Payment to Apple Services"]
        assert cmap["Payment to Apple Services"] != cmap["Payment to Airgsm Pte. Ltd."]

    def test_seed_pulls_lone_variant_into_line(self):
        # A brand resolved once (in the shared dictionary) snaps a new user's
        # single variant to the canonical.
        cmap = build_canonical_map(["HEYTEA MCLEAN VA 123"], seed_canonicals=["Heytea"])
        assert cmap["HEYTEA MCLEAN VA 123"] == "Heytea"
