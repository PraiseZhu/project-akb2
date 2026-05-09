# fixture-tool SKILL.md (fake)

This is a fake SKILL.md used by the r04-sha-drift fixture.
Its sha256 is real, but the workspace_version field in the interface has a WRONG sha prefix (000000000000).
This triggers r04 sha256_match fail.

---
slug: fixture-tool
version: fake-v0
---
