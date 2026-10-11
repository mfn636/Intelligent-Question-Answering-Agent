# 多轮会话入口
from bootstrap import create_application


def main():
    with create_application() as app:
        conversation = app.create_conversation()
        while True:
            user_input = input("用户：")
            if user_input == "exit":
                break
            result = conversation.run(user_input)
            for line in result.replies:
                print("AI：", line)
            u = result.usage
            print(f"  · 本轮 {u['llm_calls']} 次调用 | "
                  f"tokens 输入 {u['prompt_tokens']} / 输出 {u['completion_tokens']}（缓存命中 {u['cached_tokens']}） | "
                  f"整轮耗时 {u['elapsed_ms'] / 1000:.1f}s")

if __name__ == '__main__':
    main()
