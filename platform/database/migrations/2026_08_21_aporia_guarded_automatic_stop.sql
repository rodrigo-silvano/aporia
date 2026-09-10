ALTER TABLE `aporia_guarded_policy_events`
  DROP CONSTRAINT `chk_aporia_guarded_policy_event_audit`;

ALTER TABLE `aporia_guarded_policy_events`
  ADD CONSTRAINT `chk_aporia_guarded_policy_event_audit` CHECK (`event_name` = 'aporia.guarded.policy.changed' AND `event_version` = 1 AND `stream` = 'system' AND `category` = 'audit' AND `component` = 'aporia-guarded-policy' AND ((`actor_type` = 'user' AND `action_name` IN ('approve_guarded_policy','activate_guarded_policy','rollback_guarded_policy')) OR (`actor_type` = 'system' AND `action_name` = 'auto_stop_guarded_policy')) AND `lifecycle_phase` = 'succeeded' AND `outcome` = 'succeeded');
