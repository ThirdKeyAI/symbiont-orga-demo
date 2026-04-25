## deepseekv31-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deepseek/deepseek-chat-v3.1 | reflect | 25 | 1.00 | 3.0 | 2695 | 0.0017 | 6332 | 53564 | 57425 | 231 | 76 | 626.0 | 26046.1 | 13 | 0 |
| deepseek/deepseek-chat-v3.1 | task | 25 | 1.00 | 5.7 | 7051 | 0.0041 | 4241 | 24375 | 35810 | 896 | 142 | 80.5 | 689.6 | 0 | 0 |

## deepseekv31
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deepseek/deepseek-chat-v3.1 | reflect | 25 | 1.00 | 2.3 | 2231 | 0.0015 | 4604 | 51699 | 60001 | 225 | 63 | 34.5 | 61.4 | 0 | 0 |
| deepseek/deepseek-chat-v3.1 | task | 25 | 1.00 | 5.2 | 5880 | 0.0029 | 5930 | 32570 | 63942 | 473 | 129 | 79.7 | 533.5 | 0 | 0 |

## deepseekv31-tri
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deepseek/deepseek-chat-v3.1 | reflect | 25 | 1.00 | 2.6 | 2831 | 0.0016 | 5818 | 57312 | 60000 | 180 | 69 | 34.0 | 50.4 | 0 | 0 |
| deepseek/deepseek-chat-v3.1 | task | 25 | 1.00 | 5.3 | 7047 | 0.0037 | 5067 | 33060 | 35271 | 773 | 132 | 93.7 | 2551.3 | 4 | 0 |

## haiku45-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-haiku-4.5 | reflect | 25 | 1.00 | 2.0 | 3035 | 0.0053 | 5566 | 8430 | 8812 | 503 | 62 | 28.9 | 59.5 | 1 | 0 |
| anthropic/claude-haiku-4.5 | task | 25 | 1.00 | 4.0 | 7407 | 0.0090 | 5693 | 13330 | 15794 | 1109 | 121 | 72.0 | 562.7 | 0 | 0 |

## haiku45
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-haiku-4.5 | reflect | 25 | 1.00 | 2.0 | 3165 | 0.0050 | 4933 | 7693 | 9439 | 592 | 57 | 30.1 | 74.3 | 0 | 0 |
| anthropic/claude-haiku-4.5 | task | 25 | 1.00 | 4.2 | 8108 | 0.0100 | 5955 | 14430 | 15400 | 1149 | 136 | 69.3 | 558.7 | 0 | 0 |

## haiku45-tri
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-haiku-4.5 | reflect | 25 | 0.92 | 1.9 | 3066 | 0.0048 | 5243 | 6415 | 7178 | 596 | 55 | 30.3 | 49.9 | 0 | 0 |
| anthropic/claude-haiku-4.5 | task | 25 | 1.00 | 4.0 | 7562 | 0.0093 | 6312 | 10682 | 11303 | 1141 | 118 | 72.9 | 619.0 | 0 | 0 |

## minimax-m27-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| minimax/minimax-m2.7 | reflect | 25 | 1.00 | 2.0 | 2032 | 0.0010 | 9763 | 16694 | 18630 | 207 | 52 | 34.0 | 63.5 | 0 | 0 |
| minimax/minimax-m2.7 | task | 25 | 1.00 | 4.4 | 5721 | 0.0009 | 7285 | 14195 | 14934 | 696 | 120 | 79.1 | 739.3 | 0 | 0 |

## minimax-m27
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| minimax/minimax-m2.7 | reflect | 25 | 1.00 | 2.0 | 2122 | 0.0010 | 6415 | 21097 | 25482 | 233 | 53 | 33.4 | 43.2 | 0 | 0 |
| minimax/minimax-m2.7 | task | 25 | 1.00 | 4.0 | 4816 | 0.0009 | 6895 | 14476 | 25687 | 570 | 108 | 81.4 | 862.6 | 0 | 0 |

## minimax-m27-tri
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | gate_calls | gate µs/call | gate max µs | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| minimax/minimax-m2.7 | reflect | 25 | 1.00 | 2.2 | 2563 | 0.0012 | 9706 | 19879 | 30249 | 233 | 55 | 34.2 | 63.4 | 0 | 0 |
| minimax/minimax-m2.7 | task | 25 | 0.80 | 4.0 | 5563 | 0.0011 | 7233 | 22994 | 25195 | 589 | 113 | 78.4 | 689.8 | 0 | 0 |

