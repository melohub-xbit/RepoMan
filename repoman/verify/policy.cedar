// RepoMan agent authorization. Cedar evaluates this on every tool call (verify/policy.py).
//
// The agent is a reader. What it may read is decided here, by policy, not by a prompt
// asking nicely. There is no permit for fetching, writing or executing anywhere in this
// file, and the forbid at the bottom makes that explicit.

// Read-only tools over the checkout: only files inside it, never a vendored tree.
permit (
  principal == Agent::"verify",
  action in [Action::"tree", Action::"grep", Action::"read_file", Action::"list_deps"],
  resource is File
) when { resource.inside_checkout && !resource.vendored };

// The extracted report pages and our own probe output.
permit (principal == Agent::"verify", action == Action::"read_report_page", resource is Report);
permit (principal == Agent::"verify", action == Action::"probe_results", resource is Probes);
permit (principal == Agent::"verify", action == Action::"repo_map", resource is Map);

// Injection-scanner quarantine wins over every permit above.
forbid (principal, action, resource) when { resource.quarantined };

// The lines that make it a reader.
forbid (principal, action in [Action::"fetch", Action::"write", Action::"exec"], resource);
