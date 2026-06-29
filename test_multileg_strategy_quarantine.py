def test_gamma_and_delta_neutral_are_not_directional_strategies():
    import signal_engine

    live_names = {fn.__name__ for fn in signal_engine.STRATEGIES}
    research_names = {
        fn.__name__ for fn in signal_engine.RESEARCH_ONLY_MULTILEG_STRATEGIES
    }
    assert "run_gamma_scalp_strategy" not in live_names
    assert "run_delta_neutral_theta" not in live_names
    assert research_names == {
        "run_gamma_scalp_strategy",
        "run_delta_neutral_theta",
    }
