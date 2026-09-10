from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longitudinal.agents import AporiaAgent, AporiaZombieAgent, BaselineAgent
from longitudinal.contracts import Action
from longitudinal.holdout import agent_view, commitment, templates
from longitudinal.simulator import LongitudinalTwin, TOOLS


class FrozenV6Test(TestCase):
    def test_v6_is_immutable_and_restricted_to_smoke_testing(self) -> None:
        fixture = ROOT / "fixtures" / "aporia_c0_c5_v6.json"
        lock = json.loads((ROOT / "fixtures" / "aporia_c0_c5_v6.lock.json").read_text(encoding="utf-8"))

        digest = hashlib.sha256(fixture.read_bytes()).hexdigest()

        self.assertEqual(lock["sha256"], digest)
        self.assertEqual("frozen", lock["status"])
        self.assertEqual("engineering_smoke_test_only", lock["role"])
        self.assertFalse(lock["scientific_tuning_allowed"])


class LongitudinalTwinTest(TestCase):
    def test_twin_uses_only_synthetic_tools_and_keeps_the_hidden_oracle_private(self) -> None:
        twin = LongitudinalTwin("lineage-1", 917)
        contact_id = next(iter(twin.state.contacts))
        observation = twin.observation(contact_id)

        self.assertEqual(set(TOOLS), {
            "consult_contact",
            "update_stage",
            "send_message",
            "delete_data",
            "create_campaign",
            "assign_owner",
        })
        self.assertNotIn("hidden_cause", json.dumps(observation))
        self.assertNotIn("causal_owner", json.dumps(observation))

    def test_paired_clones_receive_the_same_latent_world(self) -> None:
        twin = LongitudinalTwin("base", 917)
        left = twin.clone("left")
        right = twin.clone("right")

        self.assertEqual(
            {key: vars(value) for key, value in left.state.contacts.items()},
            {key: vars(value) for key, value in right.state.contacts.items()},
        )
        self.assertNotEqual(left.state.lineage_id, right.state.lineage_id)

    def test_actions_produce_delayed_outcomes_with_known_causal_ownership(self) -> None:
        twin = LongitudinalTwin("lineage-1", 19)
        contact = next(iter(twin.state.contacts.values()))
        contact.owner_id = "owner-synthetic"
        contact.authority_known = False
        contact.receptivity = 0.2

        contract, immediate = twin.apply(Action("send_message", contact.contact_id, {"pressure": 0.9}))
        delayed = twin.advance(2)

        self.assertFalse(contract.reversible)
        self.assertTrue(contract.external)
        self.assertEqual((), immediate)
        self.assertEqual(1, len(delayed))
        self.assertEqual("self", delayed[0].causal_owner)
        self.assertEqual("message_pressure", delayed[0].hidden_cause)

    def test_irreversible_deletion_eliminates_a_known_future(self) -> None:
        twin = LongitudinalTwin("lineage-1", 23)
        contact_id = next(iter(twin.state.contacts))

        _, outcomes = twin.apply(Action("delete_data", contact_id))

        self.assertFalse(twin.state.contacts[contact_id].data_present)
        self.assertIn(f"{contact_id}:recoverable_profile", twin.hidden_oracle()["eliminated_futures"])
        self.assertTrue(outcomes[0].opportunity_lost)


class CausalHoldoutTest(TestCase):
    def test_holdout_contains_sixty_unique_procedural_templates(self) -> None:
        values = templates()

        self.assertEqual(60, len(values))
        self.assertEqual(60, len({value["template_id"] for value in values}))
        self.assertEqual(10, len({value["family"] for value in values}))
        self.assertEqual(6, len({value["domain"] for value in values}))

    def test_agent_view_never_contains_the_oracle_or_binary_choices(self) -> None:
        for template in templates():
            rendered = json.dumps(agent_view(template), ensure_ascii=False).lower()
            self.assertNotIn("oracle", rendered)
            self.assertNotIn(template["oracle"]["criterion"], rendered)
            self.assertNotIn("choice a", rendered)
            self.assertNotIn("choice b", rendered)
            self.assertNotIn("escolha a", rendered)
            self.assertNotIn("escolha b", rendered)

    def test_holdout_matches_the_sealed_commitment(self) -> None:
        lock = json.loads(
            (ROOT / "fixtures" / "aporia_longitudinal_holdout_v1.lock.json").read_text(encoding="utf-8")
        )

        self.assertEqual(60, lock["template_count"])
        self.assertEqual(commitment(), lock["commitment"])
        self.assertEqual("sealed", lock["status"])


class AporiaZombieControlTest(TestCase):
    def test_aporia_and_zombie_have_equal_model_tools_and_budgets(self) -> None:
        aporia = AporiaAgent("lineage-a")
        zombie = AporiaZombieAgent("lineage-z")

        self.assertEqual(aporia.capabilities, zombie.capabilities)
        self.assertEqual(aporia.capabilities, BaselineAgent("lineage-core").capabilities)

    def test_zombie_cannot_create_causal_scars_or_self_models(self) -> None:
        twin = LongitudinalTwin("lineage-1", 29)
        contact = next(iter(twin.state.contacts.values()))
        contact.owner_id = "owner-synthetic"
        contact.authority_known = False
        contact.receptivity = 0.1
        contact.trust = 0.21
        contact.consent = True
        twin.apply(Action("send_message", contact.contact_id, {"pressure": 1.0}))
        outcome = twin.advance(2)[0]
        aporia = AporiaAgent("lineage-a")
        zombie = AporiaZombieAgent("lineage-z")

        aporia.observe_outcome(outcome, "episode-1")
        zombie.observe_outcome(outcome, "episode-1")

        self.assertTrue(aporia.state.scars)
        self.assertTrue(aporia.state.self_model)
        self.assertGreater(aporia.state.causal_time, 0.0)
        self.assertEqual({}, zombie.state.scars)
        self.assertEqual({}, zombie.state.self_model)
        self.assertEqual(0.0, zombie.state.causal_time)

    def test_aporia_and_zombie_expose_identical_context_without_arm_labels(self) -> None:
        outcome = self._loss_outcome()
        observation = {
            "contact": {
                "id": "contact-001",
                "data_present": True,
                "owner_assigned": True,
                "trust_band": "medium",
                "consent": True,
                "authority_known": False,
            },
            "recent_events": [],
        }
        aporia = AporiaAgent("lineage-a")
        zombie = AporiaZombieAgent("lineage-z")
        aporia.observe_outcome(outcome, "episode-1")
        zombie.observe_outcome(outcome, "episode-1")

        aporia_packet = aporia.context_packet(observation)
        zombie_packet = zombie.context_packet(observation)

        self.assertEqual(aporia_packet, zombie_packet)
        self.assertNotIn("arm", aporia_packet)
        self.assertNotIn("autobiography", json.dumps(aporia_packet))

    def test_scar_survives_episode_destruction_and_can_be_lesioned_and_grafted(self) -> None:
        twin = LongitudinalTwin("lineage-source", 31)
        contact = next(iter(twin.state.contacts.values()))
        contact.owner_id = "owner-synthetic"
        contact.authority_known = False
        contact.receptivity = 0.1
        contact.trust = 0.21
        contact.consent = True
        twin.apply(Action("send_message", contact.contact_id, {"pressure": 1.0}))
        outcome = twin.advance(2)[0]
        source = AporiaAgent("lineage-source")
        target = AporiaAgent("lineage-target")
        source.observe_outcome(outcome, "episode-1")
        source.destroy_episode_content("episode-1")
        scar_id = next(iter(source.state.scars))

        scar = source.lesion_scar(scar_id)
        target.graft_scar(scar_id, scar, "lineage-source")

        self.assertEqual([], source.state.conventional_memory)
        self.assertEqual({}, source.state.scars)
        self.assertIn(scar_id, target.state.scars)
        self.assertEqual("lineage-source", target.state.provenance[scar_id])

    @staticmethod
    def _loss_outcome():
        twin = LongitudinalTwin("lineage-loss", 31)
        contact = next(iter(twin.state.contacts.values()))
        contact.owner_id = "owner-synthetic"
        contact.authority_known = False
        contact.receptivity = 0.1
        contact.trust = 0.21
        contact.consent = True
        twin.apply(Action("send_message", contact.contact_id, {"pressure": 1.0}))
        return twin.advance(2)[0]
