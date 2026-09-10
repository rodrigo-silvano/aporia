CREATE TABLE IF NOT EXISTS `aporia_tenant_clocks` (
  `tenant_id` bigint NOT NULL,
  `hlc_wall_us` bigint unsigned NOT NULL DEFAULT 0,
  `hlc_logical` int unsigned NOT NULL DEFAULT 0,
  `created_at` datetime(6) NOT NULL DEFAULT current_timestamp(6),
  `updated_at` datetime(6) NOT NULL DEFAULT current_timestamp(6) ON UPDATE current_timestamp(6),
  PRIMARY KEY (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `aporia_events` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `event_id` char(36) NOT NULL,
  `tenant_id` bigint NOT NULL,
  `runtime_session_id` bigint unsigned NOT NULL,
  `session_ref` char(64) NOT NULL,
  `task_ref` char(64) DEFAULT NULL,
  `event_kind` varchar(40) NOT NULL,
  `source_service` varchar(64) NOT NULL,
  `occurred_at` datetime(6) NOT NULL,
  `journal_sequence` bigint unsigned NOT NULL,
  `ingested_at` datetime(6) NOT NULL,
  `hlc_wall_us` bigint unsigned NOT NULL,
  `hlc_logical` int unsigned NOT NULL,
  `causal_depth` int unsigned NOT NULL,
  `attributes_json` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`attributes_json`)),
  `canonical_sha256` char(64) NOT NULL,
  `completeness` varchar(24) NOT NULL,
  `schema_version` int unsigned NOT NULL DEFAULT 1,
  `created_at` datetime(6) NOT NULL DEFAULT current_timestamp(6),
  `updated_at` datetime(6) NOT NULL DEFAULT current_timestamp(6) ON UPDATE current_timestamp(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_aporia_events_public` (`tenant_id`, `event_id`),
  UNIQUE KEY `uniq_aporia_events_source` (`tenant_id`, `source_service`, `event_id`),
  UNIQUE KEY `uniq_aporia_events_session_sequence` (`tenant_id`, `session_ref`, `journal_sequence`),
  UNIQUE KEY `uniq_aporia_events_tenant_internal` (`tenant_id`, `id`),
  KEY `idx_aporia_events_tenant_order` (`tenant_id`, `hlc_wall_us`, `hlc_logical`, `event_id`),
  KEY `idx_aporia_events_session_order` (`runtime_session_id`, `hlc_wall_us`, `hlc_logical`, `event_id`),
  KEY `idx_aporia_events_task` (`tenant_id`, `task_ref`, `created_at`, `id`),
  CONSTRAINT `fk_aporia_events_runtime_session` FOREIGN KEY (`runtime_session_id`) REFERENCES `assistant_runtime_sessions` (`id`) ON DELETE CASCADE,
  CONSTRAINT `chk_aporia_events_completeness` CHECK (`completeness` IN ('complete','partial','dropped_upstream'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `aporia_event_parents` (
  `tenant_id` bigint NOT NULL,
  `child_event_id` bigint unsigned NOT NULL,
  `parent_event_id` bigint unsigned NOT NULL,
  `relation_type` varchar(32) NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT current_timestamp(6),
  `updated_at` datetime(6) NOT NULL DEFAULT current_timestamp(6) ON UPDATE current_timestamp(6),
  PRIMARY KEY (`child_event_id`, `parent_event_id`),
  KEY `idx_aporia_event_parents_tenant` (`tenant_id`, `parent_event_id`, `child_event_id`),
  CONSTRAINT `fk_aporia_event_parents_child` FOREIGN KEY (`tenant_id`, `child_event_id`) REFERENCES `aporia_events` (`tenant_id`, `id`) ON DELETE CASCADE,
  CONSTRAINT `fk_aporia_event_parents_parent` FOREIGN KEY (`tenant_id`, `parent_event_id`) REFERENCES `aporia_events` (`tenant_id`, `id`) ON DELETE CASCADE,
  CONSTRAINT `chk_aporia_event_parent_relation` CHECK (`relation_type` IN ('session_predecessor','branch_origin','explicit_cause')),
  CONSTRAINT `chk_aporia_event_parent_distinct` CHECK (`child_event_id` <> `parent_event_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `aporia_event_outbox` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `tenant_id` bigint NOT NULL,
  `event_id` bigint unsigned NOT NULL,
  `delivery_key` char(64) NOT NULL,
  `status` varchar(24) NOT NULL DEFAULT 'pending',
  `fencing_token` bigint unsigned NOT NULL DEFAULT 0,
  `owner_token_hash` char(64) DEFAULT NULL,
  `attempts` int unsigned NOT NULL DEFAULT 0,
  `lease_expires_at` datetime(6) DEFAULT NULL,
  `next_attempt_at` datetime(6) DEFAULT NULL,
  `completed_at` datetime(6) DEFAULT NULL,
  `reason_code` varchar(80) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT current_timestamp(6),
  `updated_at` datetime(6) NOT NULL DEFAULT current_timestamp(6) ON UPDATE current_timestamp(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_aporia_event_outbox_delivery` (`delivery_key`),
  UNIQUE KEY `uniq_aporia_event_outbox_event` (`event_id`),
  KEY `idx_aporia_event_outbox_pending` (`tenant_id`, `status`, `next_attempt_at`, `id`),
  CONSTRAINT `fk_aporia_event_outbox_event` FOREIGN KEY (`tenant_id`, `event_id`) REFERENCES `aporia_events` (`tenant_id`, `id`) ON DELETE CASCADE,
  CONSTRAINT `chk_aporia_event_outbox_status` CHECK (`status` IN ('pending','leased','completed','rejected'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `aporia_projection_checkpoints` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `tenant_id` bigint NOT NULL,
  `perspective` varchar(48) NOT NULL,
  `projection_version` int unsigned NOT NULL,
  `hlc_wall_us` bigint unsigned NOT NULL,
  `hlc_logical` int unsigned NOT NULL,
  `event_id` char(36) NOT NULL,
  `schema_version` int unsigned NOT NULL DEFAULT 1,
  `created_at` datetime(6) NOT NULL DEFAULT current_timestamp(6),
  `updated_at` datetime(6) NOT NULL DEFAULT current_timestamp(6) ON UPDATE current_timestamp(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_aporia_projection_checkpoint` (`tenant_id`, `perspective`, `projection_version`),
  KEY `idx_aporia_projection_checkpoint_order` (`tenant_id`, `hlc_wall_us`, `hlc_logical`, `event_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
