import matplotlib.pyplot as plt
import pandas as pd

# Load data
file_path = 'Sigma-Ex-OF.csv'
data = pd.read_csv(file_path)

# Define categories and methods
categories = ['Normal', 'Lite']
methods = ['Original', 'Manual', 'Optimization']

# Extract values
points = []
for i, category in enumerate(categories):
    for j, method in enumerate(methods):
        value = data.iloc[i, j + 1]  # Skip first column (Sigma category)
        cost, accuracy = eval(value)  # Convert string tuple to float values
        points.append((cost, accuracy, category, method))

# Plot
plt.figure(figsize=(8, 6))
markers = {'Original': 'o', 'Manual': 's', 'Optimization': 'D'}
colors = {'Normal': 'blue', 'Lite': 'red'}

for cost, accuracy, category, method in points:
    plt.scatter(cost, accuracy, color=colors[category], marker=markers[method], label=f'{category}-{method}')

# Labels and limits
plt.xlabel('Computational Cost')
plt.ylabel('Accuracy')
plt.xlim(0, 1000)
plt.ylim(0, 3)
plt.title('Accuracy vs Computational Cost')
plt.legend()
plt.grid(True)
plt.show()
