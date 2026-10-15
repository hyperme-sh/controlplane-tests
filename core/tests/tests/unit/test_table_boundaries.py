"""Planted owner violations exercise the same static gate run by CI."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "table_checker", ROOT / "scripts/check_table_boundaries.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_current_runtime_edges_have_explicit_owners_or_named_exceptions():
    manifest = json.loads((ROOT / "scripts/ownership.json").read_text())
    assert checker.violations(ROOT, manifest) == []


@pytest.mark.parametrize(
    "source",
    [
        'query = "SELECT * FROM hypermesh_leases"',
        'query = "UPDATE public.hypermesh_leases SET status = :status"',
        "query = 'DELETE FROM \"hypermesh_leases\" WHERE id = :id'",
        'query = f"SELECT * FROM hypermesh_leases WHERE id = {lease_id}"',
        "from hypermesh_service.models import LeaseRow",
        "import hypermesh_service.models as models\nquery = models.LeaseRow",
        "from hypermesh_service import models as hm\nquery = hm.LeaseRow",
        'query = table("hypermesh_leases")',
        'query = "SELECT * FROM users, hypermesh_leases WHERE users.id = hypermesh_leases.id"',
        'query = "SELECT * FROM unnest(ARRAY[1]) u(n), hypermesh_leases h"',
        'query = "SELECT * FROM pg_catalog.unnest(ARRAY[1]) u(n), public.hypermesh_leases h"',
        'query = "ALTER TABLE IF EXISTS hypermesh_leases ADD COLUMN bad integer"',
        'query = "CREATE INDEX bad ON hypermesh_leases(id)"',
        "from hypermesh_service.models import *",
        'class Intruder:\n    __tablename__ = "hypermesh_leases"',
    ],
)
def test_cross_owner_sql_and_orm_fail(source, tmp_path):
    model = "products/hypermesh/src/hypermesh_service/models.py"
    destination = tmp_path / model
    destination.parent.mkdir(parents=True)
    destination.write_text('class LeaseRow:\n    __tablename__ = "hypermesh_leases"\n')
    path = tmp_path / "core/payment-service/src/payment_service/intruder.py"
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n")
    manifest = {
        "tables": {"hypermesh_leases": "products/hypermesh"},
        "non_orm_tables": {},
        "model_sources": {"products/hypermesh": model},
        "owners": {
            "products/hypermesh": {"package": "products/hypermesh"},
            "core/payment-service": {"package": "core/payment-service"},
        },
        "exceptions": [],
    }
    assert any(
        "owned by products/hypermesh" in error
        for error in checker.violations(tmp_path, manifest)
    )
    manifest["exceptions"] = [
        {"table": "hypermesh_leases", "source": path.relative_to(tmp_path).as_posix()}
    ]
    assert checker.violations(tmp_path, manifest) == []
    sibling = path.with_name("new_intruder.py")
    sibling.write_text(source + "\n")
    assert any(
        "new_intruder.py" in error for error in checker.violations(tmp_path, manifest)
    )


def test_sql_values_and_comments_are_not_table_references():
    assert checker.sql_tables(
        "SELECT 'FROM hypermesh_leases' -- FROM hypermesh_payouts\nFROM users"
    ) == {"users"}


def test_trigger_events_and_select_into_preserve_actual_table_references():
    assert checker.sql_tables("""
        CREATE FUNCTION public.guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE is_sealed boolean; lines bigint; total numeric;
        BEGIN
          SELECT sealed INTO is_sealed FROM public.usdc_ledger_postings FOR UPDATE;
          SELECT count(*), sum(amount_atoms) INTO lines, total
            FROM public.usdc_ledger_entries;
          RETURN NEW;
        END $$;
        CREATE TRIGGER guard BEFORE UPDATE OR DELETE ON public.usdc_ledger_entries
          FOR EACH ROW EXECUTE FUNCTION public.guard();
        UPDATE public.usdc_ledger_postings SET sealed = true;
        INSERT INTO public.usdc_ledger_entries VALUES (1);
        DELETE FROM public.usdc_ledger_entries;
    """) == {"usdc_ledger_postings", "usdc_ledger_entries"}


def test_plpgsql_into_targets_are_variables():
    assert checker.sql_tables(
        "DO $body$ DECLARE hypermesh_leases int; BEGIN "
        "SELECT count(*) INTO STRICT hypermesh_leases FROM users; END $body$;"
    ) == {"users"}
    assert checker.sql_tables(
        "CREATE FUNCTION f() RETURNS trigger AS $$ BEGIN "
        "SELECT id INTO NEW.lease_id FROM users; RETURN NEW; END $$ LANGUAGE plpgsql;"
    ) == {"users"}
    assert checker.sql_tables(
        "CREATE FUNCTION f() RETURNS void LANGUAGE 'plpgsql' AS $$ "
        "DECLARE hypermesh_leases int; BEGIN SELECT 1 INTO hypermesh_leases; "
        "INSERT INTO users DEFAULT VALUES RETURNING id INTO hypermesh_payouts; "
        "END $$;"
    ) == {"users"}


def test_top_level_select_into_targets_stay_in_scan():
    assert checker.sql_tables("SELECT * INTO hypermesh_leases FROM users") == {
        "hypermesh_leases",
        "users",
    }
    assert checker.sql_tables("SELECT id INTO public.hypermesh_leases FROM users") == {
        "hypermesh_leases",
        "users",
    }
    for modifier in ("TABLE", "TEMP", "TEMPORARY TABLE", "UNLOGGED TABLE"):
        assert {"hypermesh_leases", "users"} <= checker.sql_tables(
            f"SELECT * INTO {modifier} hypermesh_leases FROM users;"
        )


def test_select_into_in_non_plpgsql_body_stays_in_scan():
    for language in ("sql", "'sql'", '"sql"', "'SQL'"):
        assert "hypermesh_leases" in checker.sql_tables(
            f"CREATE FUNCTION f() RETURNS void LANGUAGE {language} AS $$ "
            "SELECT * INTO hypermesh_leases FROM users $$;"
        )
        assert "hypermesh_leases" in checker.sql_tables(
            "CREATE FUNCTION f() RETURNS void AS $$ "
            f"SELECT * INTO hypermesh_leases FROM users $$ LANGUAGE {language};"
        )
    assert "hypermesh_leases" in checker.sql_tables(
        "SELECT $$text$$ AS note INTO hypermesh_leases FROM users;"
    )
    assert "hypermesh_leases" in checker.sql_tables(
        "DO $$ BEGIN INSERT INTO hypermesh_leases DEFAULT VALUES; END $$;"
    )
    assert "hypermesh_leases" in checker.sql_tables(
        "DO $$ BEGIN PERFORM 1; END $$; SELECT * INTO hypermesh_leases FROM users;"
    )


def test_other_dollar_literals_cannot_declare_the_body_language():
    assert "hypermesh_leases" in checker.sql_tables(
        "CREATE PROCEDURE f(x text DEFAULT $$LANGUAGE plpgsql$$) LANGUAGE sql AS "
        "$b$ SELECT * INTO hypermesh_leases FROM host_payout_addresses $b$;"
    )
    assert "hypermesh_leases" in checker.sql_tables(
        "CREATE FUNCTION f(x text DEFAULT $d$ ; $d$) RETURNS void AS "
        "$b$ SELECT * INTO hypermesh_leases FROM users $b$ LANGUAGE sql;"
    )
    assert checker.sql_tables(
        "CREATE FUNCTION f(x text DEFAULT $d$LANGUAGE sql$d$) RETURNS void "
        "LANGUAGE plpgsql AS $b$ BEGIN SELECT 1 INTO hypermesh_leases; END $b$;"
    ) == set()

def test_nested_dynamic_sql_into_targets_stay_in_scan():
    assert checker.sql_tables(
        "DO $$ DECLARE v int; BEGIN SELECT 1 INTO v; "
        "EXECUTE $sql$SELECT * INTO hypermesh_leases FROM users;$sql$; END $$;"
    ) == {"hypermesh_leases", "users"}
    assert "hypermesh_leases" in checker.sql_tables(
        "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $b$ BEGIN "
        "EXECUTE $q$SELECT 1 INTO hypermesh_leases$q$; END $b$;"
    )
    assert "hypermesh_leases" in checker.sql_tables(
        "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS "
        "$f$INSERT INTO users SELECT * FROM hypermesh_leases$f$;"
    )

def test_or_replace_trigger_target_is_checked():
    assert checker.sql_tables(
        "CREATE OR REPLACE TRIGGER guard BEFORE INSERT ON public.hypermesh_leases "
        "FOR EACH ROW EXECUTE FUNCTION public.guard();"
    ) == {"hypermesh_leases"}

def test_trigger_target_is_checked_without_dml_references():
    assert checker.sql_tables(
        "CREATE CONSTRAINT TRIGGER guard AFTER INSERT OR UPDATE ON public.hypermesh_leases DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.guard();"
    ) == {"hypermesh_leases"}


def test_row_comparisons_and_locks_do_not_introduce_table_targets():
    assert checker.sql_tables('''
        SELECT 1 FROM public.billing_records b WHERE
        ROW(b.id, b.tenant_id) IS DISTINCT FROM ROW(NEW.id, NEW.tenant_id)
        AND b.amount IS NOT DISTINCT FROM OLD.amount FOR UPDATE;
    ''') == {'billing_records'}


def test_subscription_sql_keeps_real_table_targets_and_ignores_roles_and_builtins():
    assert checker.sql_tables("""
        REVOKE ALL ON FUNCTION public.register_group(text) FROM PUBLIC;
        INSERT INTO controlplane_event_subscriptions(consumer_group)
        SELECT t.name FROM unnest(ARRAY['one']) AS t(name)
        ON CONFLICT (consumer_group) DO UPDATE SET consumer_group=EXCLUDED.consumer_group;
        REVOKE UPDATE ON TABLE public.billing_records FROM PUBLIC;
    """) == {"controlplane_event_subscriptions", "billing_records"}
    assert checker.sql_tables("""
        SELECT * FROM pg_catalog.unnest(ARRAY(SELECT id FROM hypermesh_leases));
        REVOKE ALL ON FUNCTION public.f() FROM PUBLIC;
        DELETE FROM billing_records;
    """) == {"hypermesh_leases", "billing_records"}
