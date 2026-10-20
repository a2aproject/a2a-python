# Agent Card signatures in SDK 2.0

This next-major proposal changes Agent Card signing and verification to apply
the field-presence rules in [A2A section 8.4.1](https://a2a-protocol.org/v1.0.1/specification/#841-canonicalization-requirements)
before the existing RFC 8785 JSON canonicalizer. The SDK release version and
the A2A wire-protocol version are independent; this change does not introduce
an A2A 2.0 protocol.

## What changes

The signed payload retains required fields, explicitly present optional
defaults, and JSON values inside extension parameters. Implicit defaults on
non-required fields remain omitted. In particular:

- An explicitly present empty optional string participates in the signature.
- A required message such as `capabilities` participates even when empty.
- Extension JSON retains empty strings, objects, arrays and null values,
  including their positions inside arrays.
- The root `signatures` field remains excluded from the signed payload.

Both the public signer and verifier use this single representation. There is
no legacy-signature retry or alternate verification form. Cards whose
canonical bytes remain unchanged retain their signature validity. If the old
empty-value pruning changed a card's payload, its old signature does not verify
under the new representation. Cards with unset or default REQUIRED fields
also change because those fields were previously omitted before pruning;
correct invalid required values and re-sign these cards as well.

The required-field warning policy is unchanged. The SDK continues to warn
about missing or empty required fields; this proposal does not make invalid
cards valid or replace those warnings with new validation errors. Required
arrays must contain an element under A2A section 5.7. The migration does not
depend on signing the obsolete invalid example from the specification.

## Migrate affected cards

Coordinate the signer and verifier rollout with the SDKs that exchange your
cards. An older verifier may reject a newly signed card when it computes
different canonical bytes.

1. Retain the original unsigned card data and review the field-presence changes.
   Do not delete valid empty extension data to keep the previous signature.
2. Re-sign affected cards with their authorized issuer keys through
   `create_agent_card_signer`. Clear the old `signatures` before appending the
   replacement signature; the signing helper appends to the existing list.
3. Verify the new card through `create_signature_verifier` and an independent
   implementation of the agreed canonicalization contract. Test both signing
   directions, unchanged-byte controls, and tampering with optional presence
   and extension JSON.
4. Deploy the replacement cards no later than the route upgrade, and refresh
   cached copies as part of the coordinated rollout. `create_agent_card_routes`
   versions its weak ETag so a **pre-upgrade ETag** receives the replacement
   body instead of a 304. The marker identifies the route's representation;
   it cannot detect which representation produced a stored, external or HSM
   signature. If the upgraded route has already served a signature from the
   previous representation, re-signing unchanged content keeps that new ETag:
   purge cached card responses after re-signing, or change signed content
   (for example the card's `version`) in the replacement. Revalidation alone
   cannot repair this ordering. Subsequent re-signings under the same
   representation still revalidate normally while the verification key remains
   valid. Also purge any intermediary serving an unexpired old response without
   contacting the route, and separately implemented caches whose validators
   have not changed. Do not disable signature verification when a peer still
   uses the previous representation.

The precise cross-SDK canonicalization agreement and release window remain
upstream decisions tracked in [a2a-python #1278](https://github.com/a2aproject/a2a-python/issues/1278)
and [A2A #2122](https://github.com/a2aproject/A2A/issues/2122). This proposal
does not close either discussion by itself.

## Release boundary

`release-please-config.json` proposes `release-as: 2.0.0` for this breaking
change. The existing `always-bump-patch` strategy otherwise chooses a patch
even for a breaking commit, so a `fix!` title alone is insufficient.

Merge this change only as part of the agreed major-release plan. The release
owner must remove the one-release `release-as` override after the 2.0.0 release
and before preparing subsequent releases. The existing patch strategy then
resumes. This proposal does not manually advance the release manifest, create
a tag, or publish a package.
