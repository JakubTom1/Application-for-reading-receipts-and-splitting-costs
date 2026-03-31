def calculate_panels_sum(panels :list) -> int:
    total = 0

    for p in panels:
        max_val = 0
        n = len(p)

        for i in range(n):
            if p[i] == '9':
                continue

            for j in range(i + 1, n):
                if p[j] == '9':
                    continue

                current = int(p[i] + p[j])
                if current > max_val:
                    max_val = current

        total += max_val

    return total


/# Przykładowe dane z Twojego zadania
dane = [
    "213385519",
    "116732145",
    "819111121"
]

wynik = calculate_panels_sum(dane)
print(f"Suma wartości paneli wynosi: {wynik}")