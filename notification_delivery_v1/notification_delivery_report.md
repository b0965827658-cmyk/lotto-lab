# Notification Delivery v1

The existing `pywebpush` provider is retained. This delivery adds atomic persistent subscriptions, an approved-event queue, provider/client/open receipts, retry limits, invalid-subscription disablement, and a Product UI subscription flow.

Provider acceptance is explicitly distinct from client receipt and user open. Routine sleep, wake, observation, no-material-change, and rejected research events are not eligible.
