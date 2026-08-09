# Architecture

Approved event -> persistent queue -> existing pywebpush sender -> push provider -> browser subscription -> Service Worker -> OS notification -> client/open receipt. Prediction and Research runtimes cannot be mutated by this module.
