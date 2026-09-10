ALTER TABLE `aporia_shadow_envelopes`
  ADD UNIQUE KEY `uniq_aporia_shadow_tenant_envelope` (`tenant_id`, `envelope_id`);

ALTER TABLE `aporia_lacuna_outbox`
  ADD CONSTRAINT `fk_aporia_lacuna_outbox_envelope`
    FOREIGN KEY (`tenant_id`, `envelope_id`)
    REFERENCES `aporia_shadow_envelopes` (`tenant_id`, `envelope_id`)
    ON DELETE CASCADE;

ALTER TABLE `aporia_lacuna_runs`
  ADD CONSTRAINT `fk_aporia_lacuna_run_envelope`
    FOREIGN KEY (`tenant_id`, `envelope_id`)
    REFERENCES `aporia_shadow_envelopes` (`tenant_id`, `envelope_id`)
    ON DELETE CASCADE;
