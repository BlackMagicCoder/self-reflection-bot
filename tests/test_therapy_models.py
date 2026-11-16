import therapy_models


def test_default_model_registered():
    assert therapy_models.DEFAULT_MODEL_ID in therapy_models.MODELS


def test_get_falls_back_for_unknown_id():
    assert therapy_models.get("does-not-exist").id == therapy_models.DEFAULT_MODEL_ID
    assert therapy_models.get("").id == therapy_models.DEFAULT_MODEL_ID


def test_all_models_are_complete():
    # Every registered model must fill every slot the meta prompt relies on.
    for m in therapy_models.MODELS.values():
        assert m.id and m.name and m.description and m.intro
        assert m.methods and m.main_work and m.checkin_focus
        assert m.formulation_label and m.formulation_structure
        assert m.analysis_role and m.summary_observations


def test_checkin_focus_distinct_per_model():
    # Guards against a copy-paste bug when authoring checkin_focus — otherwise
    # every model's opening Check-in phase would look identical again, which is
    # exactly the dilution bug this field was added to fix.
    focuses = [m.checkin_focus for m in therapy_models.MODELS.values()]
    assert len(set(focuses)) == len(focuses)


def test_adaptive_main_work_has_known_failure_mode_guards():
    # Item 40b: two failure modes observed for the Adaptive model got
    # explicit guard bullets in main_work. This locks their presence in place
    # so a future prompt edit can't silently drop them again.
    main_work = " ".join(therapy_models.ADAPTIVE.main_work)
    assert "School-of-Life" in main_work and "DBT-Skills" in main_work, (
        "missing the guard against blending School-of-Life's consolatory "
        "register into a thread that actually needs a directive DBT-Skills "
        "response"
    )
    assert "generischsten" in main_work, (
        "missing the guard against defaulting to whichever approach reads "
        "most generic instead of the one the conversation actually "
        "indicates"
    )


def test_adaptive_methods_cover_every_other_model():
    # Guards the programmatic union in therapy_models._ADAPTIVE_METHODS: if a new
    # base model is registered in MODELS later without also adding it to
    # therapy_models._ADAPTIVE_SOURCE_MODELS, its techniques silently vanish from
    # the adaptive model's toolbox. Iterating MODELS.values() (not the private
    # source tuple) is what makes this catch that omission.
    adaptive = therapy_models.MODELS["adaptive"]
    combined = " ".join(adaptive.methods)
    for model in therapy_models.MODELS.values():
        if model.id == "adaptive":
            continue
        for method in model.methods:
            technique_name = method.split(" (", 1)[0]
            assert technique_name in combined, (
                f"{model.id}'s technique '{technique_name}' is missing from "
                "ADAPTIVE.methods — was it added to _ADAPTIVE_SOURCE_MODELS?"
            )
