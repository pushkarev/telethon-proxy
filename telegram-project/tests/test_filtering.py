import unittest

from telethon import types

from telegram_proxy.filtering import ensure_allowed_peer, filter_messages_bundle
from telegram_proxy.policy import CloudPolicySnapshot


def _user(user_id: int, first_name: str = "U"):
    return types.User(id=user_id, first_name=first_name, access_hash=1)


def _message(message_id: int, chat_id: int, sender_id: int, text: str = "hi"):
    return types.Message(
        id=message_id,
        peer_id=types.PeerUser(chat_id),
        from_id=types.PeerUser(sender_id),
        message=text,
        date=None,
    )


class FilteringTests(unittest.TestCase):
    def test_filters_out_messages_forbidden_by_chat(self):
        policy = CloudPolicySnapshot(folder_name="Cloud", allowed_peers={101, 202})
        allowed_message = _message(1, 101, 202)
        forbidden_message = _message(2, 303, 202)

        result = filter_messages_bundle(
            policy=policy,
            messages=[allowed_message, forbidden_message],
            chats=[],
            users=[_user(101), _user(202), _user(303)],
            allow_member_listing=False,
        )

        self.assertEqual([m.id for m in result.messages], [1])
        self.assertEqual([u.id for u in result.users], [101, 202])
        self.assertEqual(result.dropped_count, 1)

    def test_allows_member_listing_without_messaging_access(self):
        policy = CloudPolicySnapshot(folder_name="Cloud", allowed_peers={101})
        allowed_message = _message(1, 101, 999)

        result = filter_messages_bundle(
            policy=policy,
            messages=[allowed_message],
            chats=[],
            users=[_user(101), _user(999)],
            allow_member_listing=True,
        )

        self.assertEqual([u.id for u in result.users], [101, 999])

    def test_blocked_actions_raise_permission_error(self):
        policy = CloudPolicySnapshot(folder_name="Cloud", allowed_peers={101})

        with self.assertRaises(PermissionError) as ctx:
            ensure_allowed_peer(policy, types.PeerUser(202), action="sendMessage")

        self.assertIn("outside Cloud folder", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
