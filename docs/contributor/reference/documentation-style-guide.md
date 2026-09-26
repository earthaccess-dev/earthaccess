# Documentation Style Guide

This guide captures how we write documentation for earthaccess. It exists to make contributing easier and to keep our docs consistent. When in doubt, follow the examples in our existing pages.

## Keep it short

Prefer short pages, short sentences, and short paragraphs. A long page is often a sign that it should be split into smaller pages.

## Use plenty of structure

Use lots of subheadings. They make pages scannable and easier to navigate, and they help readers skip straight to what they need.

## Write with an audience in mind

We use the [Diataxis](https://diataxis.fr/) framework to decide what a page is for. It distinguishes four kinds of documentation:

- **Tutorials** teach by walking the learner through a task.
- **How-to guides** give step-by-step instructions to solve a specific problem.
- **Reference** describes technical details for readers who already know what they're looking for.
- **Explanations** provide background and context to deepen understanding.

When writing a tutorial, keep the [key principles of tutorials](https://diataxis.fr/tutorials/#key-principles) in mind — such as letting the learner work and staying focused on the goal.

## Headings

Use sentence case for headings and capitalize only the first word and proper nouns (e.g. "NASA", "ReadTheDocs"). Do not use Title Case.

Prefer noun phrases for headings that describe content, and verb phrases for headings that walk the reader through steps.

## Lists

Use bulleted lists for items that don't need a particular order, and numbered lists only for steps that must be performed in order.

## Callouts

Callouts (admonitions) draw attention to important information, but use them in moderation. Too many make a page noisy; too few let important information blend into the surrounding prose.

- Prefer callouts over emphasis (bold or italics). If you find yourself emphasizing text, consider whether a callout would serve better.
- Use collapsed callouts (`??? note`) for content that isn't critical to the main flow. If a lot of content seems to belong in a collapsed callout, consider whether it belongs in an explanation page instead.

We use the following callout types:

- `note`: information worth highlighting. Our default.
- `info`: additional context that's helpful but not essential.
- `tip`: helpful advice.
- `warning`: pitfalls and things that can go wrong.

Avoid `hint`; use `note` instead. `important` is an alias for `tip`, so use `tip`.

## Naming

See our [naming conventions](./naming-convention.md) for how we name files and directories.
