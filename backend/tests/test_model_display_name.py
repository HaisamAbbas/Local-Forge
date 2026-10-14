import pytest

from localforge.adapters.openai_compatible import model_display_name


@pytest.mark.parametrize(
    ("model_ref", "expected"),
    [
        (
            r"D:\LocalForgeData\models\qwen2.5-coder-1.5b-q4_k_m.gguf",
            "qwen2.5-coder-1.5b-q4_k_m.gguf",
        ),
        ("C:/models/llama.gguf", "llama.gguf"),
        ("/home/user/models/qwen.gguf", "qwen.gguf"),
        ("Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct"),
        ("koboldcpp/qwen2.5-coder-1.5b-q4_k_m", "koboldcpp/qwen2.5-coder-1.5b-q4_k_m"),
        ("qwen2.5:3b", "qwen2.5:3b"),
    ],
)
def test_file_path_model_ids_display_by_file_name(model_ref: str, expected: str) -> None:
    assert model_display_name(model_ref) == expected
