# Graphify Agent Explainability

## Agent Decision Reasoning
The agent determines the relationship between codebase components by parsing the AST and evaluating the generated dependency graph. It decides which execution paths to highlight by tracing the shortest semantic links between a user's query and the core function definitions.

## Data Inputs
The primary data source is the local filesystem of the target repository, which is parsed into semantic nodes and edges. Additionally, it accepts developer queries as natural language inputs to filter and traverse the generated codebase graph.

## Known Limitations
One major constraint is that the agent cannot execute dynamic runtime analysis to capture dynamically injected dependencies. Another known issue is that massive monorepos may exceed the context window if the graph is not properly pruned before analysis.
