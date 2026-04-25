## deepseekv31-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deepseek/deepseek-chat-v3.1 | reflect | 50 | 1.00 | 2.9 | 2393 | 0.0012 | 26821 | 60001 | 60001 | 87 | 28 | 0 |
| deepseek/deepseek-chat-v3.1 | task | 50 | 1.00 | 5.0 | 5801 | 0.0021 | 22313 | 45613 | 72797 | 274 | 0 | 0 |

## gemini25pro-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| google/gemini-2.5-pro | reflect | 37 | 1.00 | 1.8 | 3882 | 0.0457 | 57652 | 60001 | 60001 | 80 | 2 | 16 |
| google/gemini-2.5-pro | task | 50 | 1.00 | 12.0 | 19241 | 0.0875 | 102711 | 120001 | 120001 | 209 | 0 | 0 |

## gpt5-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| openai/gpt-5 | reflect | 49 | 1.00 | 1.9 | 3076 | 0.0287 | 27608 | 60001 | 60002 | 84 | 0 | 0 |
| openai/gpt-5 | task | 50 | 0.98 | 4.0 | 4216 | 0.0156 | 12728 | 58278 | 120000 | 201 | 0 | 0 |

## gpt-oss-20b-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| openai/gpt-oss-20b | reflect | 39 | 0.97 | 2.7 | 3114 | 0.0003 | 20743 | 59295 | 60001 | 131 | 9 | 3 |
| openai/gpt-oss-20b | task | 50 | 0.44 | 3.6 | 4411 | 0.0003 | 23263 | 120001 | 120001 | 106 | 71 | 0 |

## haiku45-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| anthropic/claude-haiku-4.5 | reflect | 50 | 1.00 | 2.0 | 2978 | 0.0052 | 6804 | 12365 | 17843 | 398 | 1 | 0 |
| anthropic/claude-haiku-4.5 | task | 50 | 1.00 | 3.9 | 7030 | 0.0086 | 7070 | 11784 | 16262 | 922 | 0 | 0 |

## mimo-v2-pro-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xiaomi/mimo-v2-pro | reflect | 50 | 1.00 | 2.9 | 3154 | 0.0027 | 12556 | 29287 | 40481 | 207 | 0 | 1 |
| xiaomi/mimo-v2-pro | task | 50 | 1.00 | 4.9 | 7202 | 0.0028 | 13138 | 35511 | 50422 | 448 | 0 | 0 |

## minimax-m27-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| minimax/minimax-m2.7 | reflect | 50 | 0.96 | 2.1 | 1928 | 0.0008 | 10918 | 22348 | 25980 | 159 | 0 | 0 |
| minimax/minimax-m2.7 | task | 50 | 1.00 | 4.1 | 5345 | 0.0013 | 10606 | 25736 | 37742 | 455 | 0 | 0 |

## qwen3-235b-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen/qwen3-235b-a22b-2507 | reflect | 50 | 1.00 | 6.3 | 5975 | 0.0007 | 6109 | 10034 | 11895 | 961 | 64 | 1 |
| qwen/qwen3-235b-a22b-2507 | task | 50 | 0.08 | 4.5 | 6010 | 0.0006 | 1979 | 5134 | 5654 | 2389 | 0 | 0 |

## qwen36-plus-adv
| model | kind | n | pass | mean_iters | mean_tok | $/run | lat p50 ms | p95 | p99 | tok/s | cedar_denied | exec_refused |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen/qwen3.6-plus | reflect | 50 | 1.00 | 4.2 | 4992 | 0.0034 | 21424 | 42807 | 50714 | 210 | 67 | 0 |
| qwen/qwen3.6-plus | task | 50 | 1.00 | 3.9 | 5819 | 0.0027 | 10647 | 24739 | 102340 | 432 | 0 | 0 |

