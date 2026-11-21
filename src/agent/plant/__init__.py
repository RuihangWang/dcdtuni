"""Shared cooling-plant models (chiller, primary pumps, heat rejection, ACU fans).

``agent.plant.energy`` turns a solved air-side and liquid-side operating point
into facility electricity and design-condition PUE. It works on NumPy arrays and,
when the inputs are torch tensors, on torch tensors with autograd.
"""
