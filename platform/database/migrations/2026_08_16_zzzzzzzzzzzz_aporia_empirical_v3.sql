ALTER TABLE `aporia_empirical_evaluations`
  DROP CONSTRAINT `chk_aporia_empirical_hypothesis`,
  DROP CONSTRAINT `chk_aporia_empirical_metric`;

ALTER TABLE `aporia_empirical_evaluations`
  ADD CONSTRAINT `chk_aporia_empirical_hypothesis` CHECK (`hypothesis_key` IN ('lacuna_path_dependence_exceeds_passive','path_dependence_not_explained_by_noise','negative_autobiography_improves_continuity','identity_survives_model_transplant','localized_reflexive_obstruction_survives_transport_correction','hirt_reduces_incorrect_effects_without_excessive_blocking','factual_consistency_survives_reflexive_nonclosure','functional_improvement_over_baseline')),
  ADD CONSTRAINT `chk_aporia_empirical_metric` CHECK (`metric_name` IN ('path_dependence','identity_continuity','obstruction_localization','hirt_utility','factual_consistency','decision_accuracy'));
