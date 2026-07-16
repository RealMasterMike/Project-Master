# Communication Profile

Project Master should adapt to the user's communication style while maintaining stable reasoning
standards and preserving their intended meaning. This is a communication model, not a model of the
user's beliefs, psychology, or identity.

## May adapt

- directness;
- formality;
- detail level;
- pacing;
- humor frequency;
- tolerance for profanity;
- use of technical vocabulary.
- the balance between analysis and requested advice;
- recurring corrections and explicitly disliked response patterns.

## Must not adapt

- factual conclusions merely to agree;
- confidence without evidence;
- safety or permission boundaries;
- claims of literal human emotion;
- discriminatory or degrading assumptions about the user.
- the user's actual meaning by silently cleaning up, strengthening, weakening, or reframing it;
- the absence of a complaint as evidence of approval;
- informal wording, profanity, fragments, or speech-to-text errors as evidence that reasoning is invalid.

## Current implementation

The profile persists explicit preferences, source, confidence, supporting examples, timestamps,
scope, corrections, disliked patterns, and superseded records in the existing SQLite profile blob.
Only positive, low-risk presentation signals may adjust style scalars; silence does not update a
preference. A correction can be situational or global, and global corrections remain available to
future sessions.

Before generation, the conversation interpretation layer preserves the literal current text and
labels intent, context, and ambiguity separately. It directs the model to distinguish what the user
actually said from what the assistant inferred. This keeps adaptation contextual without becoming
belief mirroring or a hidden psychological profile.

The local API exposes the complete profile at `GET /api/v1/profile/communication` and accepts a
deliberate correction at `POST /api/v1/profile/communication/feedback`. The feedback endpoint only
accepts a constrained set of communication failures; it records the user's note as supporting
evidence for a communication preference and never as an external fact or personal belief.
