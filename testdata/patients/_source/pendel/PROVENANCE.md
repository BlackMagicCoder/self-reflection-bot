# Provenance: pendel corpus (vendored)

> **Provenance & privacy statement (2026-08-04, confirmed by the author):** Lea, Tom,
> and the entire pendel corpus — including every named third party
> (colleagues, friends, relatives, pets) and all biographical detail — are
> **entirely fictional**, authored as test fixtures. They are not based on,
> derived from, or inspired by real, identifiable persons, and no real
> conversation data from either repository's production use is contained in
> them. Recorded here because an internal privacy/security review of 2026-08-04
> (finding PRIV-005) correctly noted that nothing in this corpus said so —
> the provenance chain below ends at "vendored from the reference
> implementation" without stating what the material *is*.

**About "the reference implementation":** wherever the simulator code and docs
in this repo mention "the reference implementation", they mean a non-public
earlier Telegram bot by the same author (a two-person check-in bot), whose
patient-simulator design this repo's `sim/` package was ported from. Its
original simulator specification is not published; the simulator is
documented by its code, its tests and `SIMULATOR_PROGRESS.md`.

These files are byte-identical copies of source material from that reference
implementation, vendored here so `sim/factledger.py`'s verbatim-source
validator works inside THIS repo without a cross-repo dependency.

Vendored on 2026-07-21. Paths below are the files' original locations in the
reference implementation:

- `couple.json` — `testdata/couples/pendel/couple.json` (voice/age notes for
  Lea and Tom — the calibration anchor, copied verbatim).
- `background_a.md`, `background_b.md` —
  `testdata/couples/pendel/background_{a,b}.md` (the two-person relationship
  backgrounds; `testdata/patients/lea/background.md` and
  `testdata/patients/tom/background.md` are single-person-POV *rewrites* of
  these, not verbatim copies — the rewrite is documented in each patient's
  own directory).
- `weeks/2026-W01.json` .. `2026-W04.json` —
  `testdata/couples/pendel/weeks/2026-W0{1,2,3,4}.json`, the scripted weekly
  check-in dialogues both patients' session fact ledgers derive from.

Not vendored (not needed): `testdata/couples/pendel/facts/2026-W0N.json` —
the reference implementation's own already-extracted, already-validated
disclosure-tagged fact ledgers for these same four weeks. This patient
corpus's `sessions/S0N.json` files were derived FROM those ledgers (weight/
disclosure/source preserved, `section_affinity` remapped to `phase_affinity`
— see each patient directory's derivation notes) rather than re-extracted
from scratch, since the hard verbatim-sourcing work was already done and
verified there. The `source` field on every non-homework fact in this
corpus is still independently verified against the vendored `weeks/*.json`
files above by `sim/factledger.py --validate`, so this corpus does not
merely trust the other repo's validation — it re-checks it against the
vendored source.

**Homework facts (id prefix `H`)** are NOT derived from the couple corpus —
there is no homework concept in a couple's weekly check-in. They are
authored bridging content for the adaptation to this bot (see
`sim/factledger.py`'s `H`-fact exemption from the verbatim-own-script
check) and are documented per-session in each patient's `sessions/S0N.json`
with an honest, non-verbatim `source` note.
