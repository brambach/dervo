# Dervo native brand assets

The wordmark is the existing outlined Caprasimo Dervo artwork from the Dervo landing-02 source, copied unchanged and
shown only at brand moments (the startup gate). The original yellow hero smile geometry is retained in DervoBrand.tsx,
with separate groups for state-driven motion. No remote font or image request is made.

19 Sep 2026: the design system's type. Instrument Sans (variable, weights 400 to 700) and Instrument Serif come from
the Fontsource npm packages `@fontsource-variable/instrument-sans@5.3.0` and `@fontsource/instrument-serif@5.3.0`;
JetBrains Mono 400 and 500 from `@fontsource/jetbrains-mono@5.3.0`, already a dependency. Latin and Latin Extended
subsets only. All three are SIL Open Font License 1.1, with each notice kept beside its font. `DervoTokens.css`
declares them. No remote font request is made.

19 Sep 2026: Manrope retired. The app's text is Instrument Sans everywhere, so `Manrope.woff2` and its OFL notice were
removed. Nothing else referenced them.

`studio-meadow.webp` is the website's hero landscape (an original generated image, see
`design/experiments/landing-02/PROVENANCE.md`), copied unchanged for the sidebar wash and brand moments.
