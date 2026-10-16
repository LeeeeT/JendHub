def source(count: int) -> str:
    return "\n".join(
        f"# Step {index}.\ndef f{index}(n: Nat) -> Nat:\n  n\n" for index in range(count)
    )
