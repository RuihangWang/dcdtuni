from pathlib import Path
def optional_output(path,package):
 if not path:return None
 root=next((p for p in package.parents if (p/'AGENTS.md').is_file()),None)
 if root is None:raise ValueError('Repository AGENTS.md root required for private output')
 out=Path(path).expanduser().resolve()
 if not out.is_relative_to(root) or out.is_relative_to(package.resolve()):raise ValueError('Output must be repository-private and outside this evidence package')
 out.mkdir(parents=True,exist_ok=True);return out
