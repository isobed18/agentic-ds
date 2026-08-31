"""Ajan asamalari tekrarlanabilir mi: seed sabitleniyor mu, nerede.

Olculen sorun (#201): ayni dosyayla iki kosum farkli sonuca vardi -- biri
"hedef bulunamadi" deyip durdu, digeri butun akisi tamamladi. Sebep,
uründe TEK BIR LLM cagrisinin seed sabitlemesiydi. `temperature=0` bunu
kapatmiyor; client.py'nin kendi yorumu da bunu soyluyor.
"""

from __future__ import annotations

from ads.llm.client import LARGE, next_ambient_seed, run_seed_scope


def test_seed_yokken_ortam_seedi_de_yok() -> None:
    """Bir kosumun disinda hicbir sey degismiyor: tek seferlik cagrilar, testler."""
    assert next_ambient_seed() is None


def test_kosum_icinde_her_cagri_seed_aliyor() -> None:
    with run_seed_scope(1000):
        assert next_ambient_seed() == 1000
        assert next_ambient_seed() == 1001
        assert next_ambient_seed() == 1002


def test_ayni_kosum_ayni_seed_dizisini_uretiyor() -> None:
    """Tekrarlanabilirligin tanimi: ayni sirada ayni seed'ler."""
    with run_seed_scope(42):
        birinci = [next_ambient_seed() for _ in range(5)]
    with run_seed_scope(42):
        ikinci = [next_ambient_seed() for _ in range(5)]
    assert birinci == ikinci


def test_cagrilar_birbirinin_AYNISI_degil() -> None:
    """Butun cagrilara tek seed vermek de tekrarlanabilir olurdu.

    Ama ayni promptun bilerek iki kez soruldugu yerlerde -- ajan paneli,
    corrective retry -- ayni ornegi cizmek cesitliligi oldururdu. Tekrarlanabilir
    olmasi, hepsinin ayni olmasi demek degil.
    """
    with run_seed_scope(7):
        cekilen = [next_ambient_seed() for _ in range(4)]
    assert len(set(cekilen)) == 4


def test_kosum_bitince_ortam_seedi_sizmiyor() -> None:
    with run_seed_scope(5):
        next_ambient_seed()
    assert next_ambient_seed() is None


def test_acik_seed_ortam_seedini_yener() -> None:
    """Bilerek seed sabitlenmis bir asama (belge-only ML verdicti, #65) korunur."""
    profil = LARGE.with_seed(99)
    with run_seed_scope(1000):
        assert profil.seed == 99


def test_kosum_seedi_run_id_den_turetiliyor_ve_kaydediliyor() -> None:
    """Kayit kaybolsa bile kosum tekrar uretilebilmeli; yine de gorunur olmali."""
    from types import SimpleNamespace

    from ads.api.service import _run_seed_for

    # `_run_seed_for` yalnizca run_id ve configuration'a bakiyor; gercek bir
    # RunState kurmak bir ArtifactStore ve AutonomyProfile gerektirirdi ve
    # bu testin olctugu seyle hicbir ilgisi yok.
    def _kosum(run_id: str):
        return SimpleNamespace(run_id=run_id, configuration={})

    a, b = _kosum("run-abc"), _kosum("run-abc")
    assert _run_seed_for(a) == _run_seed_for(b), "ayni run_id ayni seed"
    assert _run_seed_for(_kosum("run-xyz")) != _run_seed_for(a), "farkli kosum farkli seed"
    assert a.configuration["run_seed"] == _run_seed_for(a), "denetim icin kayitli"

    # Elle konmus bir seed korunur: "ayni seed'le tekrar koştur" boyle mumkun.
    elle = _kosum("run-abc")
    elle.configuration["run_seed"] = 123
    assert _run_seed_for(elle) == 123
