# SOM Analytics — Inventario completo de KPIs e indicadores

> Generado el 2026-08-16 a partir del código real de `stock_transit_allocation`
> (`models/som_analytics.py` v19.0.46.x + dashboard React `dashboard_src/`).
> Cada KPI documenta: **qué mide, cómo lo mide, por qué se mide y de qué
> modelo sale**. Al final hay un apéndice con los modelos fuente y los campos
> que se capturan de cada uno.

---

## 0. Metodología base (aplica a casi todo el tablero)

| Regla | Detalle |
|---|---|
| **Fuente de venta** | Líneas de órdenes CONFIRMADAS (`sale.order` con `state='sale'`), no facturas — mide lo comercial comprometido. |
| **Conversión USD→MXN** | Si la lista de precios de la orden es USD, se multiplica por `x_delivery_exchange_rate` de la orden (TC pactado al entregar); si no hay, por el TC Banorte del día. Todo el tablero habla MXN. |
| **Utilidad** | `venta_mxn − cantidad × x_costo_mayor` (costo **all-in** por m², campo company-dependent JSONB en `product.template`; en SQL se lee `(x_costo_mayor->>cid)::float`). Solo visible para Autorizadores de Precios. |
| **m² vs piezas** | Una línea es "área" si su UoM está en las unidades de superficie (`_area_uom_ids`); lo demás cuenta como piezas. |
| **Fechas** | Los filtros de fecha se convierten a límites UTC con zona **America/Monterrey** (`_bounds`); columnas de tipo *date* (comisiones) usan fecha plana. |
| **Refresco** | Ticker cada 60 s; pestañas cada 5 min. Drill-down real por elemento (`get_drill`). |

---

## 1. Ticker ejecutivo (`get_exec_summary`) — los vitales, siempre visibles

| KPI | Qué mide | Cómo | Por qué | Fuente |
|---|---|---|---|---|
| Venta de hoy | Monto MXN de órdenes confirmadas hoy | Σ `amount_total` convertido, `date_order::date = hoy` | Pulso diario del negocio | `sale_order` |
| **Facturación real del mes** | Facturas de cliente PUBLICADAS (timbradas) del mes | Σ `amount_total_signed` (netea notas de crédito), `state='posted'` | La medición mensual diaria oficial | `account_move` |
| **Previas sin timbrar** | Borradores de factura del mes (monto + conteo) | Igual pero `state='draft'` | Lo que falta por timbrar del mes | `account_move` |
| **Venta cajas nacionales** | Cajas y monto de líneas vendidas por empaque estándar | Σ `pack_qty` y subtotal de líneas con `standard_pack_id` en órdenes confirmadas del mes | Los SPC/decks nacionales se miden por caja | `sale_order_line` (standard_pack_som) |
| **Pedidos del mes (sistema)** | Órdenes confirmadas del mes + Δ vs mes anterior | Σ convertida por mes calendario | Comparación honesta vs facturación real — "cierre en el mes no garantizado" | `sale_order` |
| Utilidad del mes / Margen | Utilidad all-in y % | Metodología base | Rentabilidad, no solo volumen | `sale_order_line` + `product_template.x_costo_mayor` |
| m² vendidos del mes | Volumen físico | Σ qty de líneas de área | El negocio se mueve en m² | `sale_order_line` |
| Dinero en bancos | Saldo contable de TODOS los diarios banco/efectivo (incluye Caja Nacional) | Balance por diario (`get_bank_balances`) | Liquidez inmediata | `account_move_line` por `account_journal` |
| Me deben / Debo | Cartera viva por cobrar / pagar | Σ `amount_residual_signed` de facturas posted con residual | Exposición de caja | `account_move` |
| Inventario en patio | m² internos + holds activos | Σ quants internos de productos m²; conteo de holds no cancelados | Capital en piso | `stock_quant`, `stock_lot_hold_order` |
| Antigüedad de inventario | Días promedio del stock, ponderado por m² | `NOW() − stock_lot.create_date` ponderado por `quant.quantity` | Capital estancado | `stock_quant` + `stock_lot` |
| En el agua | m² y contenedores en tránsito vivo | Σ `stock_transit_line.product_uom_qty` de viajes no entregados/cancelados | Lo que viene en camino | `stock_transit_voyage/line` |
| TC Banorte | Tipo de cambio del día + autorizaciones de precio pendientes | Servicio TC + `price_authorization state='pending'` | Contexto cambiario y cuellos comerciales | externo + `price_authorization` |

---

## 2. Pestaña RESUMEN (`_dom_resumen`)

Combina el pack de ventas (§3), inventario (§5), tránsito (§7) y finanzas (§10):
`venta_mxn, utilidad_mxn, margen_pct, m2_vendidos, piezas_vendidas, ordenes`
+ `inv_disponible_m2, inv_valor_mxn, transit_m2, por_cobrar, por_pagar`,
con aging de inventario, tránsito por estatus y totales financieros.
**Por qué**: una sola pantalla tipo TV con la foto completa. *(En móvil esta
vista no existe: arranca en Ventas.)*

## 3. Pestaña COMERCIAL (`_dom_comercial`)

Pack base de ventas (por mes, por vendedor, por nivel de precio, top productos
por utilidad, top clientes, por categoría, órdenes del corte) más:

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `conversion_pct` / `cotizaciones_abiertas` | Conversión cotización→orden | Órdenes `sale` ÷ (sale+draft+sent) del periodo | Efectividad comercial |
| `descuento_mxn` / `descuentos_con_auth` | Dinero descontado y cuántos pasaron por autorización | Σ descuentos de líneas; cruce con `price_authorization` | Fugas de margen y disciplina |
| `pct_via_arquitecto` + `architects` | % de venta que llega vía embajador (especificador) y top embajadores | Órdenes con `x_architect_id` ÷ total | Canal de especificación |
| `bloqueadas_precio` / `bloqueadas_monto` | Órdenes detenidas por autorización de precio | `price_authorization` pendiente ligada a la orden | Cuánto dinero está detenido |
| `iva_solicitadas` / `iva_aprobadas` | Solicitudes de factura sin IVA | Modelo de solicitudes IVA | Control fiscal |
| `exposicion_usd/mxn/ordenes` | Venta USD entregada aún no cobrada | Órdenes USD con saldo | Riesgo cambiario |
| `auth_solicitudes/aprobadas/pendientes/horas_resolucion` | Flujo de autorizaciones de precio y su velocidad | `price_authorization` + timestamps | Cuello de botella comercial |
| `comisiones_mxn` + `commissions` | Comisiones devengadas por rol/vendedor | `commission.move` (om_advanced_commission), fecha plana | Costo de venta comercial |
| `realizacion_pct` | Precio real vs lista | Venta ÷ (qty × precio lista) | Qué tanto se respeta el tarifario |
| `reincidencias_piso` | Clientes que repiten precio piso | Conteo de reincidencias | Detección de abuso de descuento |
| `auth_delta_pct` | Δ% promedio autorizado vs precio original | Promedio del delta en autorizaciones | Cuánto se cede al autorizar |
| `fx_realizado_mxn` / `fx_ordenes` | Utilidad/pérdida cambiaria realizada | TC pactado vs TC del día en cobros USD | Resultado FX real |
| `desc_aprobados` | Descuentos aprobados en el periodo | `price_authorization` aprobadas | Volumen de excepciones |

## 4. Pestaña MATERIALES (`get_time_to_sell`)

| Indicador | Qué mide | Cómo | Por qué |
|---|---|---|---|
| Días promedio en vender | Rotación por material | Promedio (fecha venta − `stock_lot.create_date`) de lotes vendidos 12m | Qué gira y qué no |
| Edad stock (años/meses) | Antigüedad del stock actual por material | `NOW() − create_date` de lotes en stock (gráfico excluye >3000 días legacy) | **Capital estancado** |
| m² vendidos 12m / m² en stock / lotes | Volúmenes de contexto | Sumas directas | Dimensionar el problema |

Fuente: `stock_move_line` (salidas a cliente) + `stock_quant` + `stock_lot`.

## 5. Pestaña INVENTARIO (`_inventory_pack` / `_dom_inventario`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `disponible_m2` / `lotes` | Stock físico interno | Σ quants internos (>0) de productos m² | Base de venta |
| `hold_m2` / `holds_activos` | Apartado comercial | Quants con `x_tiene_hold` / holds vivos | Apartado ≠ vendido |
| `valor_mxn` | Valor del inventario | m² × `x_costo_mayor` | Capital inmovilizado |
| `edad_prom_dias` | Antigüedad promedio ponderada | Igual que ticker | Salud del stock |
| `lotes_foto_pct` (+con/sin) | Cobertura fotográfica de lotes | Lotes con fotografías ÷ total | Sin foto no se vende en catálogo |
| `aging` / `aging_by_date` | Buckets de antigüedad (m², lotes, valor) | Por rangos de días | Dónde está el capital viejo |
| `top_stock` | Materiales con más m² | Ranking | Concentración |

Fuente: `stock_quant` + `stock_lot` (dimensiones `x_alto/x_ancho/x_grosor`,
`x_bloque`, `x_atado`, fotos) + `product_template`.

## 6. Pestaña COMPRAS (`_dom_compras`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `compras_mxn` | Compra confirmada del periodo (normalizada a MXN) | `purchase_order` confirmadas + TC | Volumen de abastecimiento |
| `ordenes` / `proveedores` | Actividad y diversificación | Conteos | Dependencia de proveedores |
| `lead_time_dias` / `lead_time_ocs` | Días confirmación→recepción medidos | OC `date_approve` → recepción `date_done` | Planeación de compra (alimenta Restock) |
| `discrepancias` | OCs con diferencias pedido vs embarcado/recibido | Cruce con `x_qty_solicitada_original` / `x_qty_embarcada` | Control del proveedor |
| `costo_log_m2_mxn` | Costo logístico promedio por m² | Tarifa all-in del tarifario (POL→POD, `freight_tariff`) ÷ m² del embarque | Costo real de traer material |
| `tc_usado` | TC de la normalización | Banorte del día | Transparencia |

## 7. Pestaña TRÁNSITO (`_transit_pack`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `total_m2` / `embarques` | Lo vivo en el agua | Σ líneas de viajes no entregados/cancelados | Pipeline físico |
| `pendientes_publicar` / `dias_a_publicar` | Viajes sin publicar inventario y su demora promedio | `transit_inventory_published_at − create_date` | El material no vendible hasta publicar |
| `prevendido_pct` | % del tránsito ya comprometido | Líneas asignadas (partner/orden) ÷ total | Cuánto llega ya vendido |
| `eta_desviacion_dias` / `eta_desviados` | Desviación ETA real vs original | `eta − eta_original` | Confiabilidad de navieras |
| `ligas_portal` / `ligas_sin_acceso_7d` / `ligas_avance_pct` / `ligas_terminadas` | Uso del portal de proveedores | `supplier_access` (`last_access`, avance de captura vía `_portal_progress`) | Que el proveedor capture su PL a tiempo |
| `by_status` / `voyages` / `by_supplier` / `eta_months` | Cortes por estatus/proveedor/mes | Agregaciones | Torre de control |

Fuente: `stock_transit_voyage`, `stock_transit_line`, `supplier_access`.

## 8. Pestaña RECEPCIONES (`_dom_recepciones`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `fisicas_periodo` / `m2_recibidos` | Recepciones validadas y volumen | Pickings entrada `done` por semana | Ritmo de entrada |
| `pedimento_pct` / `lotes_sin_pedimento` | Cobertura aduanal del lote | `stock_lot.x_pedimento` presente ÷ lotes | Obligación fiscal/aduanal |
| `exactitud_pct` / `con_devolucion` | Recepciones sin devolución posterior | Entradas − las que generaron devolución | Calidad de recepción |
| `etiquetado_24h_pct` | Lotes etiquetados en <24 h | Timestamp de impresión de etiqueta vs creación | Disciplina de almacén |
| `faltantes_piezas` / `faltantes_m2` | Faltantes detectados en worksheet | `stock_picking.x_ws_missing_pieces/x_ws_missing_m2` | Merma de importación |
| `bajas_scrap` | Material dado de baja | `stock_scrap` done del periodo | Pérdidas |

## 9. Pestaña TALLER (`_dom_taller`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `area_in_m2` / `area_out_m2` / `merma_m2` / `merma_pct` | Entra vs sale del taller y su merma | Sumas de órdenes de taller | La merma es dinero |
| `en_taller` / `by_state` | WIP actual | `workshop.order` por estado | Carga de trabajo |
| `backlog_dias` | Antigüedad del backlog | Promedio de días de órdenes abiertas | Cuello del taller |
| `lead_time_dias` / `terminadas` | Ciclo promedio y throughput | Fechas inicio→fin de terminadas | Capacidad real |
| `reclasificaciones` / `pasadas` | Lotes reprocesados (sufijos `-R2/-R3/-R4+`) | Regex sobre `stock_lot.name` | Retrabajo (folios de pasadas) |

## 10. Pestaña ENTREGAS (`_dom_entregas`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `firmadas_app` / `manuales` | Remisiones firmadas digitalmente vs a mano | `sale_delivery_document.signed_at` / flag manual | Adopción del flujo digital |
| `en_ruta` | Entregas en curso | Remisiones confirmadas sin cerrar | Operación viva |
| `credito_informal_mxn` | Entregas autorizadas sin pago completo | Σ saldo de órdenes con auth manual de entrega (`sale_delivery_auth`) | Riesgo: material fuera sin cobrar |
| `ciclo_dias` / `ciclo_muestras` | Días confirmación de orden → entrega firmada | Promedio | Promesa de entrega |
| `devoluciones` + `returns` | Devoluciones por motivo | Documentos de devolución | Calidad de despacho |
| `ocupacion_pct` | Aprovechamiento del vehículo | Σ m² cargados ÷ `vehicle_capacity_sqm` por remisión | Costo logístico de reparto |
| `paradas_gps` | Puntos de ruta registrados | `sale_delivery_route_point` | Trazabilidad del mapa de entregas |
| `cobrado_al_entregar_pct` | % del total de la orden ya pagado al firmar | `delivery_paid_amount ÷ amount_total` al `signed_at` | Disciplina de cobro |
| `pago_post_entrega` | Días promedio entrega→pago por cliente | `paid_at − signed_at` | Quién paga tarde después de recibir |

## 11. Pestaña FINANCIERO (`_dom_financiero`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `por_cobrar` / `por_pagar` / `neto` | Cartera viva ambos lados | Residuales de facturas posted (MXN al TC del registro) | Posición de caja |
| `dso_dias` | Días de venta en la calle | Por cobrar ÷ facturación 12m × 365 | Velocidad de cobro |
| `vencido_mxn` / `vencido_pct` | Cartera vencida | Residual con `invoice_date_due` pasada | Riesgo de incobrable |
| `facturado_mes` (+prev, +MoM) | Facturación timbrada mensual | `amount_total_signed` posted | Tendencia de facturación |
| `efectivo_sin_aplicar` / `recibos_sin_aplicar` / `efectivo_aplicado` | Recibos de efectivo vs su aplicación contable | `cash.receipt` (cash_receipt_voucher) | Dinero recibido no reflejado |
| `comprobantes_pendientes` / `comprobantes_monto` | Comprobantes de pago sin validar | `sale_payment_proof state='pending'` | Cobranza en limbo |
| Aging AR/AP + top deudores | Buckets de antigüedad y ranking | Por rangos de atraso, drill a factura por factura | Priorizar cobranza |
| Flujo 90 días | Entradas vs salidas proyectadas por vencimientos | Vencimientos AR/AP futuros | Anticipar quiebres de caja |

## 12. Pestaña CONTROL (`_dom_control`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `pendientes_total` + `bandeja` | Pendientes operativos por tipo (autorizaciones, recepciones, publicaciones, worksheets, etc.) | Batería de queries `add(label, sql)` | To-do ejecutivo del sistema |
| `productos_ajuste_precio` + `price_adjust` | Productos cuyo precio quedó fuera de la escalera vigente | Comparación contra escalera company-dependent | Higiene del tarifario |
| `lotes_en_stock` / `sin_proyecto_pct` / `sin_referencia_pct` | Calidad de captura de lotes | % de lotes sin proyecto/referencia | Datos completos = trazabilidad |
| `placas_foto_pct`, `lotes_foto_pct` (+uploaders, +por mes) | Cobertura y ritmo fotográfico, por usuario | `supplier.shipment.block.image` y fotos de lote | Quién sube fotos y quién no |
| `fx` (USD/MXN, EUR/USD, EUR/MXN) | Tipos de cambio de referencia | Banorte + ECB | Contexto para compras EUR |
| `ficha` | Completitud de ficha de producto | Campos clave presentes | Catálogo confiable |

## 13. Pestaña PRONÓSTICOS (`_dom_pronosticos`)

| KPI | Qué mide | Cómo | Por qué |
|---|---|---|---|
| `venta_proximo_mes` / `venta_3m` | Proyección de venta | Regresión/promedio móvil sobre historia mensual (`meses_historia`) | Planear compra y caja |
| `tendencia_pct` | Pendiente de la tendencia | Sobre la serie mensual | ¿Creciendo o cayendo? |
| `entra_90d` / `sale_90d` / `flujo_90d` | Caja proyectada 90 días | Vencimientos AR vs AP | Anticipación financiera |
| `cobertura` | Meses de inventario vs ritmo de venta | Stock ÷ venta mensual por material | Antesala del módulo Restock |

## 14. Bancos (`get_bank_balances`)

Balance contable por diario de tipo banco/efectivo (incluye **Caja Nacional**),
con total consolidado MXN. Fuente: `account_move_line` agrupado por
`account_journal`. **Por qué**: el "dinero en bancos" del ticker, desglosable.

---

## Apéndice — Modelos fuente y qué se captura de cada uno

| Modelo | Campos usados por Analytics | Otros campos relevantes que captura el sistema |
|---|---|---|
| `sale.order` | state, date_order, amount_total, pricelist→currency, `x_delivery_exchange_rate`, user_id, partner_id, `x_architect_id` (embajador), delivery_paid_amount | job name/proyecto, forma de pago, política de entrega, uso CFDI, términos, segundo vendedor, watermark de estado, `delivery_auth_state` (candado de entregas), compromiso de lotes por línea |
| `sale.order.line` | product, qty, uom (m² vs pieza), price_subtotal, nivel de precio, descuentos, `standard_pack_id`/`pack_qty` (cajas) | `x_lot_breakdown_json` (parcialidades), lot_ids, modos Pedir/Asignar/Taller, precio personalizado (PP) |
| `product.template` | uom, categoría, `x_costo_mayor` (all-in JSONB por compañía), `x_costo_usd_edit`, escalera de precios | empaques estándar, nombres origen (máscara por proveedor), fotos/galería |
| `stock.quant` | quantity, reserved_quantity, location.usage, `x_tiene_hold` | ubicación jerárquica (último hijo), tránsito SOM/TRANSIT (usage normalizado) |
| `stock.lot` | create_date (edad), name (pasadas `-R2+`), `x_pedimento`, fotos, dimensiones | `x_bloque`, `x_atado`, `x_grupo`, `x_alto/x_ancho/x_grosor`, `x_tipo` (placa/formato/pieza), proveedor, contenedor, origen, `x_detalles_placa`, active (reclasificación archiva) |
| `stock.move.line` | quantity, date, ubicaciones origen/destino (consumo = salidas a customer netas de devoluciones) | lotes por movimiento, picking de origen |
| `stock.picking` | date_done, tipo entrada/salida, `x_ws_missing_pieces/x_ws_missing_m2` | worksheet/PL, supplier_bl, accesos de portal |
| `stock.transit.voyage` / `stock.transit.line` | custom_status, eta/eta_original/etd, contenedor, líneas (qty, allocation_status, partner/orden), publicación (`transit_inventory_published_at`) | booking, naviera, BL, proveedor, facturas de carga, recepción física ligada |
| `supplier.access` | create/last_access, avance de captura, expiración | URL portal, liga por OC/embarque |
| `purchase.order` / línea | state, date_approve, partner, montos, `x_qty_solicitada_original`, `x_qty_embarcada`, qty_received | ruta logística del tarifario (país, forwarder, POL/POD, naviera, ETD), calendario de pagos SOMGROUP, asignaciones por cliente, docs VUCEM |
| `freight_tariff` | all_in por ruta POL→POD | vigencias, forwarder/naviera (etiquetas de partner del tarifario) |
| `account.move` | move_type, state (posted/draft = timbrada/previa), amount_total_signed, amount_residual_signed, invoice_date_due, currency | CFDI/pagos, conciliaciones parciales (para fechas de pago reales) |
| `account.journal` / `account.move.line` | tipo banco/efectivo, balances | Caja Nacional (auto-creada), Efectivo |
| `cash.receipt` / `cash.entry` | montos aplicados vs sin aplicar | doble control de caja manual, ligas a OV/OC |
| `sale.payment.proof` | state, amount | comprobantes de pago del cliente |
| `price.authorization` | state, montos, timestamps, deltas | flujo de autorización de descuentos ≥2,000 MXN, costos USD para el autorizador |
| `commission.move` | monto, fecha (plana), rol (embajador/constructora/referidor/interno) | reglas de comisión por rol |
| `sale.delivery.document` (+line, +route_point) | tipo remisión, state, signed_at, vehicle_capacity_sqm, qty_done, puntos GPS | firma digital, redeliveries, devoluciones con motivo, mapa de entregas |
| `workshop.order` | estado, fechas, áreas entrada/salida | cadena de OTs desde venta, folios genéricos de pasada, merma por orden |
| `stock.lot.hold.order` | state (activos) | embajador, proyecto, vencimiento del apartado, parcialidades |
| `stock.scrap` | qty done del periodo | bajas masivas (writeoff hereda selector) |
| `supplier.shipment.block.image` | conteo de fotos por bloque/usuario/mes | fotos de bloque para el visor del inventario |

---

### Notas de mantenimiento

- Este documento refleja el código a la fecha del encabezado; al agregar KPIs
  nuevos en `som_analytics.py`, añadir la fila correspondiente aquí.
- Complementos fuera de Analytics que consumen las mismas fuentes:
  **Restock** (radar de recompra: consumo, tránsito libre, lead times
  medidos) y los tableros de **Recepciones/Salidas** y **Mapa de entregas**.
