# Implementation Plan: Room Humidity Simulator

## Overview

This plan implements the Room Humidity Simulator in **Python**, using **Hypothesis** for property-based testing and **pytest** for example-based unit tests and integration tests. The implementation follows the layered architecture from the design: a stateless `Psychrometrics` physics module, domain components (`Room`, `Fan`, `Coil`, and the `Disturbance` interface with its two built-in implementations), and a `Simulator` orchestrator.

Tasks build bottom-up so that each step is validated early: pure physics first, then domain components and their algorithms, then disturbances, then the orchestrator that wires everything together with the strict per-step update order. Each of the 14 correctness properties is covered by exactly one Hypothesis property test (minimum 100 iterations, tagged `Feature: room-humidity-simulator, Property {number}: {property_text}`), complemented by example-based unit tests and 1-3 integration tests.

## Tasks

- [x] 1. Set up project structure, shared result/error types, and config models
  - Create the package layout (`room_humidity_simulator/` with `psychrometrics.py`, `room.py`, `fan.py`, `coil.py`, `disturbances.py`, `simulator.py`, `results.py`, `config.py`) and a `tests/` directory
  - Define a structured result/error type (`ConfigError`/`ValidationResult` with a descriptive field-specific message) returned by validation instead of raising raw exceptions
  - Define the configuration data models `RoomConfig`, `FanConfig`, `CoilConfig`, `TimeStepConfig` with their documented fields and permitted ranges as constants
  - Add `pytest` and `hypothesis` to the project dependencies and set up the test runner configuration
  - _Requirements: 1.1, 3.1, 4.8, 7.3_

- [x] 2. Implement the Psychrometrics physics module
  - [x] 2.1 Implement the pure saturation and dew-point functions
    - Implement `saturation_vapor_pressure(T)` using August-Roche-Magnus `es = 6.112 * exp(17.62*T / (243.12 + T))`
    - Implement `saturation_content_density(T)` as `216.7 * es(T) / (273.15 + T)` and `saturation_content_grams(T, volume)` as `density * volume`
    - Implement `dew_point(specific_humidity, T)` via the inverted Magnus formula on the actual vapor pressure of the moved air
    - Implement `relative_humidity_percent(abs_grams, T, volume)` as `clamp(100 * abs_grams / saturation_content_grams(T, volume), 0.0, 100.0)` rounded to one decimal, returning `0.0` when `abs_grams == 0` or `S(T, V) == 0`
    - _Requirements: 2.1, 2.4, 4.3, 4.4, 2.5, 2.6, 2.7_

  - [x]* 2.2 Write property test for relative-humidity derivation
    - **Property 4: Relative-humidity derivation**
    - Generators cover `H = 0` and `S(T, V) = 0` edge cases
    - Tag: `Feature: room-humidity-simulator, Property 4: Relative-humidity derivation`
    - **Validates: Requirements 2.1, 2.2, 2.4, 2.6, 2.7**

  - [x]* 2.3 Write unit tests for Psychrometrics edge cases
    - Test monotonicity/finiteness at temperature bounds (-50, 100) and the zero-capacity reporting path
    - _Requirements: 2.4, 2.7_

- [x] 3. Implement the Room domain component
  - [x] 3.1 Implement Room state container and derived accessors
    - Store `volume`, `temperature`, and full-precision `absolute_humidity_grams`
    - Implement `specific_humidity()` as `absolute_humidity_grams / volume`, `relative_humidity()` delegating to `Psychrometrics`, and getters for volume/temperature/absolute humidity reported in `[0.0, 999.9]`
    - Implement the internal `set_absolute_humidity(g)` that always clamps to `[0, saturation_content_grams(T, V)]`
    - _Requirements: 1.7, 1.8, 1.9, 5.2, 4.3, 4.7, 5.9, 7.4_

  - [x]* 3.2 Write unit tests for Room getters and clamping
    - Confirm a freshly configured Room exposes configured values within range (Req 1.9); confirm `set_absolute_humidity` clamps below 0 and above saturation
    - _Requirements: 1.7, 1.8, 1.9_

- [x] 4. Implement the Fan domain component
  - [x] 4.1 Implement Fan with moved-air computation
    - Store `flow_rate` and implement `moved_air_volume(dt)` as `flow_rate * dt`, returning `0.0` when `flow_rate == 0.0`
    - Expose the current flow rate via a getter
    - _Requirements: 3.5, 3.6, 3.7_

  - [x]* 4.2 Write unit test for Fan getter and zero-flow behavior
    - Confirm getter returns the configured flow rate (Req 3.7) and `moved_air_volume` is `0.0` at zero flow (Req 3.6)
    - _Requirements: 3.6, 3.7_

- [x] 5. Implement the Coil domain component and removal algorithm
  - [x] 5.1 Implement Coil with the dew-point/temperature-gated removal algorithm
    - Store `surface_temperature` and `condensation_capacity`
    - Implement `removal(moved_air_volume, room, dt)` returning grams removed, always `>= 0`: return `0.0` when `surface_temperature >= room.temperature`; return `0.0` when `dew_point(room.specific_humidity(), room.temperature) <= surface_temperature`; otherwise return `min(condensation_capacity_for_step, room.specific_humidity() * moved_air_volume)` bounded by `room.absolute_humidity_grams`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x]* 5.2 Write property test for coil removal bounds
    - **Property 7: Coil removal is bounded by the lesser of capacity and available moisture**
    - Generators include `flow_rate = 0`
    - Tag: `Feature: room-humidity-simulator, Property 7: Coil removal is bounded by the lesser of capacity and available moisture`
    - **Validates: Requirements 4.1, 4.2**

  - [x]* 5.3 Write property test for coil dew-point and temperature gating
    - **Property 8: Coil dew-point and temperature gating**
    - Generators include surface temperature above/below room temperature and dew point exactly equal to surface temperature
    - Tag: `Feature: room-humidity-simulator, Property 8: Coil dew-point and temperature gating`
    - **Validates: Requirements 4.3, 4.4, 4.5**

- [x] 6. Checkpoint - Ensure physics and domain components pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement the Disturbance interface and built-in disturbance types
  - [x] 7.1 Define the Disturbance interface and the two built-in implementations
    - Define the `Disturbance` interface with `is_active()` and `contribution(dt)`
    - Implement `Person_Disturbance` and `Boiling_Water_Disturbance`, each storing `moisture_rate` and returning `moisture_rate * dt` from `contribution(dt)` for `dt > 0`
    - _Requirements: 5.1, 5.2, 6.1, 6.2_

  - [x]* 7.2 Write property test for disturbance contribution
    - **Property 5: Disturbance contribution is rate times duration**
    - Generators use moisture rates in `[0.0, 1000.0]` and `dt > 0`
    - Tag: `Feature: room-humidity-simulator, Property 5: Disturbance contribution is rate times duration`
    - **Validates: Requirements 5.1, 5.2, 6.2**

  - [x]* 7.3 Write unit test for custom disturbance incorporation
    - Provide a caller-supplied custom disturbance conforming to the interface and confirm it is incorporated without changes to Coil or Fan
    - _Requirements: 6.1, 6.3_

- [x] 8. Implement Simulator configuration and validation
  - [x] 8.1 Implement configure() with eager validation and state preservation
    - Validate room volume `[0.01, 100000]`, temperature `[-50, 100]`, initial humidity `[0.0, 1000]`, fan flow rate `[0.0, 10.0]` (including non-numeric/undefined), coil capacity `>= 0.0`, and time-step duration `(0.0, 3600]`
    - On any violation, reject the operation, create no Room / retain the prior valid component and state, and return a descriptive field-specific error matching the Error Handling table
    - On valid input, build `Room`, `Fan`, `Coil` and expose getters returning configured values
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 3.1, 3.2, 3.3, 3.4, 4.8, 7.3, 1.9, 3.7_

  - [x]* 8.2 Write property test for valid configuration round-trips
    - **Property 11: Valid configuration round-trips**
    - Tag: `Feature: room-humidity-simulator, Property 11: Valid configuration round-trips`
    - **Validates: Requirements 1.1, 3.1, 3.7, 1.9**

  - [x]* 8.3 Write property test for invalid configuration rejection with state retention
    - **Property 12: Invalid configuration is rejected and prior state is retained**
    - Generators include out-of-range values for every validated field plus boundary values
    - Tag: `Feature: room-humidity-simulator, Property 12: Invalid configuration is rejected and prior state is retained`
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 3.2, 3.3, 4.8, 5.6, 5.7, 7.3, 7.7**

  - [x]* 8.4 Write unit tests for exact configuration error indications
    - Assert the non-numeric/undefined fan flow rate returns the "flow rate invalid" error (Req 3.4) and the zero-saturation-capacity reporting returns the not-derivable indication (Req 2.7)
    - _Requirements: 3.4, 2.7_

- [x] 9. Implement Simulator disturbance management
  - [x] 9.1 Implement add/remove disturbance management with validation
    - Implement `add_disturbance(d)`: reject when 100 are already active ("maximum active disturbances reached") or moisture rate out of `[0.0, 1000.0]` ("moisture rate out of range"), retaining the active set / prior room state on error; otherwise include it starting with the next step
    - Implement `remove_disturbance(d)`: remove an active disturbance (effective next step) or return "disturbance not found" leaving the active set unchanged
    - Maintain the active-disturbance set bounded to `[0, 100]`
    - _Requirements: 5.3, 5.4, 5.6, 5.7, 5.8, 6.5_

  - [x]* 9.2 Write property test for total disturbance contribution
    - **Property 6: Total disturbance contribution is the arithmetic sum**
    - Generators use active-disturbance sets of size `0..100`
    - Tag: `Feature: room-humidity-simulator, Property 6: Total disturbance contribution is the arithmetic sum`
    - **Validates: Requirements 5.5, 6.4, 6.5**

  - [x]* 9.3 Write property test for disturbance membership timing
    - **Property 13: Disturbance membership takes effect on the next step**
    - Tag: `Feature: room-humidity-simulator, Property 13: Disturbance membership takes effect on the next step`
    - **Validates: Requirements 5.3, 5.4**

  - [x]* 9.4 Write unit test for removal of a non-active disturbance
    - Confirm removing a non-active disturbance returns the "disturbance not found" error and leaves the active set unchanged
    - _Requirements: 5.8_

- [x] 10. Checkpoint - Ensure disturbances and configuration pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement the Simulator step engine (strict update order)
  - [x] 11.1 Implement the single-step advance with the mandated update order
    - Implement `advance(dt)` validating `dt` then executing, in order: sum valid disturbance contributions (`clamp_invalid` excludes negative/NaN/non-numeric returns and records an identifying error); add to room; cap room at `S(T, V)`; compute moved-air volume and coil removal; subtract removed mass; clamp room at `0.0`; increase `simulation_time` by `dt`
    - Update and expose `room_state()` (absolute + relative humidity) after the step
    - _Requirements: 7.1, 7.2, 7.4, 7.5, 4.6, 5.9, 6.6_

  - [x] 11.2 Implement multi-step advance(dt, n)
    - Implement `advance(dt, n)` that repeats the single-step sequence `n` times for `n >= 1`; reject `n < 1` with "time step count invalid" leaving `simulation_time` and absolute humidity unchanged
    - _Requirements: 7.6, 7.7_

  - [x]* 11.3 Write property test for reported humidity bounds
    - **Property 1: Reported humidity stays within bounds**
    - Tag: `Feature: room-humidity-simulator, Property 1: Reported humidity stays within bounds`
    - **Validates: Requirements 1.7, 1.8, 2.5, 7.5**

  - [x]* 11.4 Write property test for non-negative absolute humidity
    - **Property 2: Absolute humidity is never negative**
    - Generators include large disturbance-free dehumidification runs
    - Tag: `Feature: room-humidity-simulator, Property 2: Absolute humidity is never negative`
    - **Validates: Requirements 4.7, 7.4**

  - [x]* 11.5 Write property test for the saturation cap
    - **Property 3: Saturation cap**
    - Tag: `Feature: room-humidity-simulator, Property 3: Saturation cap`
    - **Validates: Requirements 2.3, 5.9**

  - [x]* 11.6 Write property test for step mass balance and update order
    - **Property 9: Step mass balance and update order**
    - Tag: `Feature: room-humidity-simulator, Property 9: Step mass balance and update order`
    - **Validates: Requirements 4.6, 7.1**

  - [x]* 11.7 Write property test for step composition and simulation-time accumulation
    - **Property 10: Step composition and simulation-time accumulation**
    - Generators use advance counts around the `n >= 1` boundary
    - Tag: `Feature: room-humidity-simulator, Property 10: Step composition and simulation-time accumulation`
    - **Validates: Requirements 7.2, 7.6**

  - [x]* 11.8 Write property test for exclusion of invalid disturbance returns
    - **Property 14: Invalid disturbance returns are excluded**
    - Generators include disturbances returning negative/NaN/non-numeric values
    - Tag: `Feature: room-humidity-simulator, Property 14: Invalid disturbance returns are excluded`
    - **Validates: Requirements 6.6**

- [x] 12. Integration and end-to-end wiring
  - [x]* 12.1 Write end-to-end integration tests
    - Exercise the full configure -> add disturbances -> advance(dt, n) -> query pipeline across 1-3 scenarios (representative dehumidification and moisture-loading trajectories)
    - Assert the non-negative and saturation-capped invariants hold throughout and that `simulation_time` accumulates to `n * dt`
    - _Requirements: 7.1, 7.2, 7.5, 2.3, 4.7_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Phase 2 Tasks (Compressor and Energy Model)

> **Increment note:** The tasks below are a **later increment** (Phase 2) covering Requirements 8-12 and design Properties 15-23. They build on the finished Phase 1 components (`Psychrometrics`, `Room`, `Fan`, `Coil`, `Disturbance`, and the `Simulator` step engine): the `Compressor`, coil coupling, and energy accounting all extend that Phase 1 code. Phase 2 leaf tasks are scheduled together with Phase 1 in the single unified `## Task Dependency Graph` at the end of this document. Property tests continue to use **Hypothesis** (minimum 100 iterations, tagged `Feature: room-humidity-simulator, Property {number}: {property_text}`) and example/integration tests use **pytest**, matching the Phase 1 toolchain.

- [x] 14. Implement the latent and sensible heat helpers in Psychrometrics
  - [x] 14.1 Add the energy helper functions for the heat model
    - Add `air_density` (`1.2 kg/m^3`) and `cp` (`1005 J/(kg·K)`) as named constants
    - Implement the latent-heat helper `latent_heat_joules(removed_grams)` as `removed_grams * 2260` (Latent_Heat_Of_Condensation, J/g)
    - Implement the sensible-heat helper `sensible_heat_joules(moved_air_volume, room_temp, coil_surface_temp)` as `moved_air_volume * air_density * cp * (room_temp - coil_surface_temp)` only when `room_temp > coil_surface_temp` and `moved_air_volume > 0`, returning `0.0` otherwise (in particular `0.0` when `moved_air_volume == 0`)
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x]* 14.2 Write property test for heat extracted (latent plus sensible)
    - **Property 19: Heat extracted is latent plus sensible heat**
    - Generators include `moved_air_volume = 0` and setpoints at/above room temperature so the sensible term is `0.0`
    - Tag: `Feature: room-humidity-simulator, Property 19: Heat extracted is latent plus sensible heat`
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

- [x] 15. Implement the Compressor domain component
  - [x] 15.1 Implement Compressor configuration, state, validation, and getters
    - Store `cop`, `setpoint_temperature`, and `state` with an initial `state` of `off`; expose getters for `cop`, `setpoint_temperature`, and `state` (exactly one of `running`/`off`)
    - Validate `cop` as finite with `0.0 < cop <= 20.0` (`<= 0.0` -> "COP must be greater than 0.0"; `> 20.0` -> "COP exceeds maximum"; non-numeric/undefined -> "COP invalid") and `setpoint_temperature` as finite in `[-50, 100]` (out of range -> "setpoint temperature out of range"; non-numeric/undefined -> "setpoint temperature invalid"), rejecting invalid values while preserving prior valid state
    - Implement `coil_surface_temperature(room_temperature)` returning `setpoint_temperature` when `running` and `room_temperature` when `off`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9_

  - [x]* 15.2 Write property test for compressor configuration round-trip and initial state
    - **Property 15: Compressor configuration round-trips and starts off**
    - Generators use COP in and around `(0.0, 20.0]` and setpoint in `[-50, 100]`
    - Tag: `Feature: room-humidity-simulator, Property 15: Compressor configuration round-trips and starts off`
    - **Validates: Requirements 8.1, 8.7, 8.8**

  - [x]* 15.3 Write property test for invalid compressor configuration rejection with state retention
    - **Property 16: Invalid compressor configuration is rejected and prior state is retained**
    - Generators include COP `<= 0.0`, `> 20.0`, NaN/undefined and setpoint out of range / non-numeric
    - Tag: `Feature: room-humidity-simulator, Property 16: Invalid compressor configuration is rejected and prior state is retained`
    - **Validates: Requirements 8.2, 8.3, 8.4, 8.5, 8.6**

  - [x]* 15.4 Write unit tests for exact compressor error messages and state enum
    - Assert the "COP invalid" message for non-numeric/undefined COP (Req 8.4), the "setpoint temperature invalid" message for non-numeric/undefined setpoint (Req 8.6), and that `state` is exposed as exactly one of `running`/`off` (Req 8.9)
    - _Requirements: 8.4, 8.6, 8.9_

- [x] 16. Implement Compressor-to-Coil coupling in the Simulator
  - [x] 16.1 Wire the compressor into the step to drive the coil surface temperature
    - Add `configure_compressor(cop, setpoint_temperature)` that validates and attaches the optional Compressor (retaining prior state on error) and `set_compressor_state(running|off)` that records a pending state promoted to `state` at the start of the next step (no-op on the current step)
    - At the start of each step, when a Compressor is configured, promote the pending state then set `coil.surface_temperature = compressor.coil_surface_temperature(room.temperature)` before coil removal: `setpoint_temperature` when running, `room.temperature` when off (so the Phase 1 Req 4.5 gating yields `0.0 g` removed while off)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x]* 16.2 Write property test for compressor-to-coil coupling
    - **Property 17: Compressor couples the coil surface temperature to its state**
    - Generators include setpoints below and at/above room temperature
    - Tag: `Feature: room-humidity-simulator, Property 17: Compressor couples the coil surface temperature to its state`
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4**

  - [x]* 16.3 Write property test for deferred compressor state changes
    - **Property 18: Compressor state changes take effect on the next time step**
    - Generators use `running`/`off` toggle sequences across steps
    - Tag: `Feature: room-humidity-simulator, Property 18: Compressor state changes take effect on the next time step`
    - **Validates: Requirements 9.5**

- [x] 17. Implement energy accounting in the step engine
  - [x] 17.1 Compute per-step heat, electrical energy, cumulative energy, and power draw
    - After coil removal, when a Compressor is configured and running, compute `heat_extracted = max(latent_heat_joules(removed_grams) + sensible_heat_joules(moved_air_volume, room_temp, coil_surface_temp), 0.0)` and `electrical = heat_extracted / cop`; set `electrical = 0.0` when off or when running with nothing removed and zero sensible heat
    - Accumulate `energy_consumed += electrical` (monotonically non-decreasing) and set `power_draw = electrical / dt` when `dt > 0`, else `0.0`; `power_draw = 0.0` when off or when electrical is `0.0`
    - Initialize `energy_consumed = 0.0` and `power_draw = 0.0` before the first step; expose `energy_consumed_joules` and `power_draw_watts` getters (`>= 0.0`)
    - _Requirements: 10.5, 10.6, 10.7, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8_

  - [x]* 17.2 Write property test for electrical energy per step
    - **Property 20: Electrical energy per step equals heat divided by COP**
    - Generators include running-with-zero-removal and `moved_air_volume = 0` so electrical is `0.0`
    - Tag: `Feature: room-humidity-simulator, Property 20: Electrical energy per step equals heat divided by COP`
    - **Validates: Requirements 10.5, 10.6, 10.7**

  - [x]* 17.3 Write property test for cumulative energy and power draw
    - **Property 21: Cumulative energy is monotonic and power is energy per unit time**
    - Generators use `running`/`off` toggle sequences and zero-electrical steps
    - Tag: `Feature: room-humidity-simulator, Property 21: Cumulative energy is monotonic and power is energy per unit time`
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 11.7, 11.8**

  - [x]* 17.4 Write unit test for energy/power initialization before first step
    - Confirm `energy_consumed` is `0.0 J` and `power_draw` is `0.0 W` before the first Time_Step is advanced, and a representative running-then-off trajectory matches known expected energy/power values
    - _Requirements: 11.6_

- [x] 18. Implement optional-compressor injection and backward compatibility
  - [x] 18.1 Preserve Phase 1 behavior when no compressor is configured and support mid-run injection
    - Ensure that with no Compressor configured the step skips both Phase 2 hooks: the Coil keeps its configured constant surface temperature, no energy is tracked, and `energy_consumed`/`power_draw` remain `0.0` after every step
    - Ensure adding a Compressor to a running Simulator leaves the current Room Absolute_Humidity, Room temperature, and Simulation_Time unchanged at the moment of addition, with coupling and energy accounting beginning on the next step and `energy_consumed` continuing from its current value
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x]* 18.2 Write property test for backward-compatibility parity
    - **Property 23: Without a compressor, behavior and outputs match Phase 1**
    - Run random valid Room/Fan/Coil configs, disturbance sets of size `0..100`, and `advance(dt, n)` sequences against a Phase 1 reference and the Phase 2 no-compressor engine; assert `|ΔH| <= 0.1 g`, `|ΔRH| <= 0.1 pp`, and `energy_consumed = 0.0`, `power_draw = 0.0` after each step
    - Tag: `Feature: room-humidity-simulator, Property 23: Without a compressor, behavior and outputs match Phase 1`
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.4**

  - [x]* 18.3 Write property test for mid-run compressor addition
    - **Property 22: Adding a compressor preserves state and defers its effect**
    - Generators advance a no-compressor Simulator through a random number of steps before adding the Compressor, then assert the preserved quantities and deferred first-effect step
    - Tag: `Feature: room-humidity-simulator, Property 22: Adding a compressor preserves state and defers its effect`
    - **Validates: Requirements 12.5**

- [x] 19. Checkpoint - Ensure Phase 2 compressor and energy tasks pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 20. Phase 2 integration and end-to-end wiring
  - [x]* 20.1 Write Phase 2 end-to-end integration tests
    - Exercise the full configure -> configure compressor -> set state -> advance(dt, n) -> query energy/power pipeline across 1-3 scenarios
    - Assert `energy_consumed` is monotonic, `power_draw` tracks the most recent step, coil coupling switches dehumidification on/off with the compressor state, and the Phase 1 non-negative and saturation-capped invariants still hold throughout
    - _Requirements: 9.1, 9.2, 11.1, 11.2, 11.4, 11.5_

  - [x]* 20.2 Write Phase 2 example tests for coupling and backward-compat parity
    - Add example tests for compressor config error messages, running/off coupling behavior, and a no-compressor backward-compatibility parity check within `0.1 g` / `0.1 pp` tolerance
    - _Requirements: 8.4, 8.6, 9.1, 9.2, 12.4_

- [x] 21. Final Phase 2 checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Coil Model Refinement

- [x] 22. Replace the coil removal rule with a physically based recirculating coil model
  - [x] 22.1 Model removal as `V dq/dt = -min(capacity, F * effectiveness * (q - q_out))`, solved exactly over the step, with `q_out` the coil outlet humidity; capacity becomes a rate in g/s and a coil `effectiveness` in `[0, 1]` (default 0.85) is added and validated
    - _Requirements: 4.1, 4.2, 4.8, 4.9, 4.10_
  - [x] 22.2 Scale the sensible-heat term by the coil effectiveness
    - _Requirements: 10.3_
  - [x]* 22.3 Rewrite Property 7 against numerical integration and add Property 24 (monotonic in humidity and subcooling)
    - **Validates: Requirements 4.1, 4.2, 4.10**

## Power-Limited Compressor

- [x] 23. Limit the compressor to a maximum absorbed power
  - [x] 23.1 Add `max_power_w` (default 400 W, validated `> 0`) to the Compressor; cooling is limited to `cop * max_power_w`
    - _Requirements: 8.10_
  - [x] 23.2 Hold the coil at the setpoint only while its heat load fits; otherwise solve the coil temperature where the heat load equals `cop * max_power_w`, with sub-steps when power-limited and condensing; flag power-limited steps
    - _Requirements: 9.1, 9.6, 11.9_
  - [x]* 23.3 Update Properties 17, 18, 20, 21, 22 for power-limited steps; add Property 25 and hand-computed power-limited examples
    - **Validates: Requirements 9.6, 11.9**

## COP Lookup Table

- [x] 24. Make the compressor COP a function of temperature through a lookup table
  - [x] 24.1 Add `CopTable` (1D over coil temperature or 2D over room x coil temperature; linear/bilinear interpolation clamped at the edges) with structured validation
    - _Requirements: 8.11_
  - [x] 24.2 Accept a `CopTable` as `cop`; use `COP(T_coil, T_room)` in the power-limited balance and per-sub-step energy; report the effective COP of each step
    - _Requirements: 8.11, 9.6, 10.5_
  - [x]* 24.3 Add Properties 26 and 27 plus interpolation, validation and efficiency examples
    - **Validates: Requirements 8.11**

## Closed-Loop Control

- [x] 25. Let a control algorithm drive the fan speed and the on/off compressor
  - [x] 25.1 Add `Simulator.set_fan_flow` (validated, effective next step)
    - _Requirements: 13.1_
  - [x] 25.2 Add `ClosedLoop` with the controller interface, fan speeds with power, RH sensor model, compressor protections and KPIs (`room_humidity_simulator/control.py`)
    - _Requirements: 13.2, 14.1-14.4, 15.1_
  - [x] 25.3 Add baseline controllers (always on, hysteresis, staged hysteresis) and the `controllers/my_controller.py` template
  - [x] 25.4 Web UI: algorithm selection from `controllers/*.py` (reloaded each run), parameters, comparison of two algorithms, KPI table, fan and energy charts
  - [x]* 25.5 Property test: protections never violated for any command sequence; tests for sensor model, KPIs, baselines, file reload and error location
    - **Validates: Requirements 13-15**

## Open Window

- [x] 26. Add an open-window disturbance with gusty, reproducible airflow
  - [x] 26.1 `Open_Window_Disturbance`: exact signed air exchange toward the outdoor humidity, Ornstein-Uhlenbeck gusts, validation
    - _Requirements: 16.1, 16.3, 16.4_
  - [x] 26.2 Simulator accepts signed `exchange_grams(room, dt)`; plain disturbances still may not go negative
    - _Requirements: 16.2, 6.6_
  - [x] 26.3 Web UI: "+ open window" source with airflow, gusts, outdoor temperature and RH
  - [x]* 26.4 Tests: exact exponential, no overshoot (property), gust statistics and reproducibility, A/B wind fairness, validation
    - **Validates: Requirements 16**

## Frost and Defrost

- [x] 27. Model frost on a sub-zero coil and the defrost cycle
  - [x] 27.1 Heat of fusion below 0 °C; frost-reduced coil effectiveness
    - _Requirements: 17.1, 17.2_
  - [x] 27.2 `DefrostSettings` and the defrost state machine in the Compressor; melting, re-evaporation and coil at 0 °C in the step
    - _Requirements: 17.3, 17.4, 17.5_
  - [x] 27.3 Closed loop: `defrosting` measurement, fan kept on, defrost KPIs; web UI settings, lanes, KPIs
    - _Requirements: 17.6_
  - [x]* 27.4 Tests: no frost at or above 0 °C (property), frost = frozen water, cycle timing, min/max duration, water balance, closed loop
  - [x] 27.5 Setpoint sweeps at 10-26 °C rooms: default coil setpoint set to +1 °C

## On/Off Compressor

- [x] 28. Make the default compressor a true on/off unit
  - [x] 28.1 Full power while running; coil temperature from its heat balance with thermal mass (exact linearized sub-steps), frost and melting at 0 °C included
    - _Requirements: 18.1-18.4_
  - [x] 28.2 `mode="on_off" | "modulating"`, coil heat capacity and still-air coupling, validation; Phase 2 tests pinned to modulating
    - _Requirements: 18.5, 18.6_
  - [x] 28.3 Web UI: compressor type, setpoint only for modulating, coil heat capacity for on/off
  - [x]* 28.4 Tests: full power, setpoint ignored, exact pull-down/warm-up, airflow vs coil temperature, emergent frost and defrost, coil never above room (property), validation

## Notes

- Implementation language: **Python**; property-based tests use **Hypothesis**, example/integration tests use **pytest**.
- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each of the 14 correctness properties is implemented by a single Hypothesis property test running a minimum of 100 iterations and tagged `Feature: room-humidity-simulator, Property {number}: {property_text}`.
- Each task references specific requirements (and property tests reference their design property) for traceability.
- Checkpoints ensure incremental validation at natural layer boundaries.
- **Phase 2 (tasks 14-21)** is a later increment covering the optional Compressor and energy model (Requirements 8-12, Properties 15-23). It builds on the finished Phase 1 components (`Psychrometrics`, `Room`, `Fan`, `Coil`, `Disturbance`, and the `Simulator` step engine). Phase 2 leaf tasks are now scheduled together with Phase 1 in the single unified `## Task Dependency Graph` below.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "4.1", "7.1", "15.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.1", "4.2", "7.2", "7.3", "14.1", "15.2", "15.3", "15.4"] },
    { "id": 3, "tasks": ["3.2", "5.1", "14.2"] },
    { "id": 4, "tasks": ["5.2", "5.3", "8.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "8.4", "9.1"] },
    { "id": 6, "tasks": ["9.2", "9.3", "9.4", "11.1"] },
    { "id": 7, "tasks": ["11.2", "11.3", "11.4", "11.5", "11.6", "11.8"] },
    { "id": 8, "tasks": ["11.7", "12.1", "16.1"] },
    { "id": 9, "tasks": ["16.2", "16.3", "17.1"] },
    { "id": 10, "tasks": ["17.2", "17.3", "17.4", "18.1"] },
    { "id": 11, "tasks": ["18.2", "18.3", "20.1", "20.2"] }
  ]
}
```
