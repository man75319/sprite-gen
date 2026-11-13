# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen char`: a character and its actions, each action's generations kept as versions (docs/char.md).

`ledger` is what lies on disk (character.json, action.json, version.json, the lock), `char_plan` turns a
character's locked spec and one action into the `video-set` it runs and the prompt it sends, `char_cmd` is the verb.
"""
