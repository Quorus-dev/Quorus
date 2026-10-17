# Teardown: Agent Mail

## Core Architecture & Mechanism
An asynchronous asynchronous messaging protocol designed specifically for AI agents, modeling communication on SMTP/IMAP standards with cryptographic signing and structured JSON payload attachments.

## What It Does Better Than Quorus
- **Robust Asynchronous Delivery:** Guarantees message delivery and queuing even when recipient agents are offline or sleeping.
- **Threaded Conversation History:** Structured email-like threading (`In-Reply-To`, `References`) prevents context fragmentation in long-running multi-agent debates.

## What Nobody Else Does (Unique Innovations)
- **Cryptographic Agent Identity Verification:** Signs every agent message with an ephemeral Ed25519 keypair, preventing message spoofing or prompt injection in shared channels.

## Takeaways for Quorus
Adopt cryptographic signing for relay messages and add robust offline queuing and threaded message metadata to our social protocol.
