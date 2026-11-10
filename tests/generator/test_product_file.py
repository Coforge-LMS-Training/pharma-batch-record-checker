"""Extra synthetic products read from a TOML file (generate-batch-records --products)."""

from pathlib import Path

import pytest

from pharma_batch_record_checker.generator.masters import (
    CAPSULE_PROCESS,
    TABLET_PROCESS,
    ProductConfig,
)
from pharma_batch_record_checker.generator.product_file import (
    FileProduct,
    load_products,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "products.toml"

# A clean-only capsule product: every measured CAPSULE_PROCESS step has limits.
CAPSULE_TOML = """\
[[product]]
master_record_id = "MBR-PLC100"
master_version   = "2"
product_name     = "Placeprol 100 mg hard capsules"
batch_size       = "100000"
batch_size_unit  = "capsules"
excipient        = "lactose"
process          = "capsule"
id_letter        = "C"

[product.limits]
"S-DISP-01"  = ["9.95", "10.05"]
"S-DISP-02"  = ["4.98", "5.02"]
"S-BLEND-01" = ["15", "20"]
"S-BLEND-02" = ["", "3.0"]
"S-BLEND-03" = ["2", "4"]
"S-ENC-01"   = ["35", "55"]
"S-ENC-02"   = ["120", "135"]
"S-ENC-03"   = ["98.0", ""]
"S-PACK-02"  = ["60", "60"]
"""


def write(tmp_path, text, name="products.toml"):
    """Write text to a TOML file in tmp_path and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def load_text(tmp_path, text):
    """load_products on a file holding text."""
    return load_products(write(tmp_path, text))


def refused(tmp_path, text):
    """The ValueError message load_products raises for a file holding text."""
    path = write(tmp_path, text)
    with pytest.raises(ValueError) as excinfo:
        load_products(path)
    message = str(excinfo.value)
    assert message.startswith(f"{path}: ")
    return message


def test_example_file_loads_the_synthetic_tablet_product():
    """examples/products.toml gives one tablet product that reuses SYN500's defects."""
    (product,) = load_products(EXAMPLE)
    assert product.prefix == "S"
    assert product.defects_like == "MBR-SYN500"
    assert product.product.master_record_id == "MBR-SYN100"
    assert product.product.process == TABLET_PROCESS
    assert product.product.limits["S-COMP-01"] == ("190", "210")


def test_capsule_product_loads_as_a_product_config(tmp_path):
    """Every field is kept as written; "capsule" maps to CAPSULE_PROCESS."""
    (product,) = load_text(tmp_path, CAPSULE_TOML)
    assert product == FileProduct(
        product=ProductConfig(
            master_record_id="MBR-PLC100",
            master_version="2",
            product_name="Placeprol 100 mg hard capsules",
            batch_size="100000",
            batch_size_unit="capsules",
            excipient="lactose",
            process=CAPSULE_PROCESS,
            limits=product.product.limits,
        ),
        prefix="C",
        defects_like=None,
    )
    assert product.product.limits["S-BLEND-02"] == ("", "3.0")
    assert product.product.limits["S-PACK-02"] == ("60", "60")


def test_several_products_load_in_file_order(tmp_path):
    """Products come back in the order the file lists them."""
    second = CAPSULE_TOML.replace("MBR-PLC100", "MBR-PLC050").replace(
        'id_letter        = "C"', 'id_letter        = "D"'
    )
    products = load_text(tmp_path, CAPSULE_TOML + "\n" + second)
    assert [p.product.master_record_id for p in products] == [
        "MBR-PLC100",
        "MBR-PLC050",
    ]


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        ('batch_size       = "100000"', "batch_size = 100000", "batch_size"),
        ('"S-ENC-01"   = ["35", "55"]', '"S-ENC-01" = [35, 55]', "S-ENC-01"),
        ('"S-ENC-01"   = ["35", "55"]', '"S-ENC-01" = ["35"]', "S-ENC-01"),
        ('process          = "capsule"', 'process = "softgel"', "process"),
        ('id_letter        = "C"', 'id_letter = "CC"', "id_letter"),
        ('id_letter        = "C"', 'id_letter = "c"', "id_letter"),
        ('id_letter        = "C"', 'id_letter = "1"', "id_letter"),
        ('excipient        = "lactose"\n', "", "excipient"),
        (
            'excipient        = "lactose"',
            'excipient = "lactose"\nexcipeint = "x"',
            "excipeint",
        ),
    ],
    ids=[
        "number_batch_size",
        "number_limits",
        "one_limit",
        "unknown_process",
        "two_letters",
        "lower_case_letter",
        "digit_letter",
        "missing_field",
        "unknown_key",
    ],
)
def test_invalid_fields_are_refused_by_name(tmp_path, old, new, field):
    """A wrong, missing or unknown field is refused, naming the field.

    Numbers must be quoted text: they are rejected, never converted.
    """
    assert old in CAPSULE_TOML
    assert field in refused(tmp_path, CAPSULE_TOML.replace(old, new))


def test_file_without_products_is_refused(tmp_path):
    """A file with no [[product]] entries adds nothing, so it is an error."""
    assert "product" in refused(tmp_path, "# nothing here\n")
    assert "product" in refused(tmp_path, "product = []\n")


def test_invalid_toml_is_refused(tmp_path):
    """A TOML syntax error names the file."""
    assert "invalid TOML" in refused(tmp_path, "[[product]\n")


def test_missing_file_is_refused(tmp_path):
    """A path that doesn't exist names the file."""
    path = tmp_path / "nope.toml"
    with pytest.raises(ValueError, match=f"^{path}: cannot read"):
        load_products(path)


def test_limits_must_cover_exactly_the_measured_steps(tmp_path):
    """The master checks run at load: no missing limits, none on sign-off steps."""
    missing = CAPSULE_TOML.replace('"S-ENC-01"   = ["35", "55"]\n', "")
    assert "no limits for measured steps ['S-ENC-01']" in refused(tmp_path, missing)
    extra = CAPSULE_TOML + '"S-PACK-01" = ["1", "2"]\n'
    assert "sign-off-only steps ['S-PACK-01']" in refused(tmp_path, extra)


def test_master_row_rules_apply_at_load(tmp_path):
    """Values must also meet the CSV contract, e.g. min not above max."""
    flipped = CAPSULE_TOML.replace('["35", "55"]', '["55", "35"]')
    assert "MBR-PLC100 v2 step S-ENC-01" in refused(tmp_path, flipped)
