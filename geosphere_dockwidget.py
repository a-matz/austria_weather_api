# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GeosphereAPIDockWidget
                                 A QGIS plugin
 Connection to the Geosphere (former ZAMG) API
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                             -------------------
        begin                : 2023-03-17
        git sha              : $Format:%H$
        copyright            : (C) 2023 by Armin Matzl
        email                : arminmatzl@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os

from qgis.PyQt import QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import Qt, QSignalBlocker, QVariant, pyqtSignal, QTranslator, QCoreApplication, QDateTime
from qgis.PyQt.QtWidgets import QProgressBar, QDialog, QGridLayout, QProgressDialog, QMessageBox, QTableWidgetItem, QCheckBox
import processing
from qgis.core import (QgsMapLayerProxyModel, QgsGeometry, 
                      QgsProject, QgsFeature, QgsPoint, edit, QgsVectorLayer, QgsMeshLayer, QgsRasterLayer,
                      QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsField, QgsPointXY,
                      QgsProcessing, Qgis, NULL)
from qgis.gui import QgsMapToolExtent, QgsMapToolPan, QgsMapToolEmitPoint, QgsMessageBar
import requests
import pandas as pd
import pickle
import json
import time
from datetime import datetime,timedelta
import webbrowser

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'geosphere_dockwidget_base.ui'))

"""
to do: change maptool when vectorlayer is deleted
"""
class GeosphereAPIDockWidget(QtWidgets.QDockWidget, FORM_CLASS):

    closingPlugin = pyqtSignal()

    def __init__(self,iface, parent=None):
        """Constructor."""
        super(GeosphereAPIDockWidget, self).__init__(parent)
        # Set up the user interface from Designer.
        # After setupUI you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://doc.qt.io/qt-5/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-
        self.setCursor(Qt.WaitCursor)
        self.iface = iface
        self.setupUi(self)
        self.combobox_typ.currentIndexChanged.connect(self.update_modus)
        self.combobox_modus.currentIndexChanged.connect(self.update_id)
        self.combobox_id.currentIndexChanged.connect(self.load_metadata)
        self.button_load_stations.clicked.connect(self.load_stations_to_canvas)
        self.extent.setOutputCrs(QgsCoordinateReferenceSystem.fromEpsgId(4326))
        self.extent.setMapCanvas(self.iface.mapCanvas())
        self.button_download.clicked.connect(self.download)
        self.combobox_outformat.currentIndexChanged.connect(self.update_filepath)
        self.button_removeStationsAll.clicked.connect(lambda: self.remove_all_entries(self.table_stations))
        self.button_removeStations.clicked.connect(lambda: self.remove_selected(self.table_stations))
        self.button_removePointsAll.clicked.connect(self.delete_all_ts_points)
        self.button_removePoints.clicked.connect(self.delete_selected_ts_points)
        self.button_unselectAll.clicked.connect(self.unselect_all_parameters)
        self.button_selectAll.clicked.connect(self.select_all_parameters)
        self.button_addBasemap.clicked.connect(self.add_basemap)
        self.parameter_filter.textEdited.connect(self.filter_parameter_table)
        self.combobox_pointlayer.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.button_loadPoints.clicked.connect(self.load_points_from_layer)
        #define select by rectangle tool
        self.select_tool = QgsMapToolExtent(self.iface.mapCanvas())
        self.select_tool.extentChanged.connect(self.get_features)
        self.select_tool.setToolName("geosphere_api_select")
        self.button_select.clicked.connect(lambda: self.iface.mapCanvas().setMapTool(self.select_tool))
        #define select point tool
        self.point_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.point_tool.canvasClicked.connect(self.select_point)
        self.point_tool.setToolName("geosphere_api_pointTool")
        self.button_selectPoint.clicked.connect(self.activate_select_point)
        #button description, open browser 
        self.button_description.clicked.connect(self.open_description_url)

        QgsProject.instance().layerRemoved.connect(self.layer_removed)
        
        self.station_list = []

        self.start_time.setTimeSpec(Qt.UTC)
        self.end_time.setTimeSpec(Qt.UTC)

        self.load_datasets()
        self.setCursor(Qt.ArrowCursor)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        try:
            QgsProject.instance().removeMapLayer(self.point_layer)
            self.iface.mapCanvas().refresh()
        except:
            pass
        event.accept()
    
    def tr(self, message):
        return QCoreApplication.translate('GeosphereAPI', message)
    
    def add_basemap(self):
        uri = "crs=EPSG:3857&dpiMode=7&format=image/jpeg&layers=bmaphidpi&styles=normal&tileMatrixSet=google3857&url=https://www.basemap.at/wmts/1.0.0/WMTSCapabilities.xml&http-header:referer="
        QgsProject.instance().addMapLayer(QgsRasterLayer(uri, "Basemap.at", "wms"))

    #check if a necessary layer is removed by user
    def layer_removed(self,l):
        try:
            self.current_layer.id()
        except:
            self.button_select.setEnabled(False)
            if self.iface.mapCanvas().mapTool() != None:
                if self.iface.mapCanvas().mapTool().toolName() == "geosphere_api_select":
                    self.iface.mapCanvas().setMapTool(QgsMapToolPan(self.iface.mapCanvas()))
        try:
            self.point_layer.id()
        except:
            self.table_points.clear()
            self.table_points.setColumnCount(0)
            self.table_points.setRowCount(0)
            if self.iface.mapCanvas().mapTool() != None:
                if self.iface.mapCanvas().mapTool().toolName() == "geosphere_api_pointTool":
                    self.iface.mapCanvas().setMapTool(QgsMapToolPan(self.iface.mapCanvas()))



    #load available datasets when plugin starts
    #saves available datasets, repeated every 30 days to check for updates
    def load_datasets(self):
        dataset_file = os.path.join(os.path.dirname(__file__),"geosphere_datasets.pkl")
        if not os.path.isfile(dataset_file):
            load_from_web = True
        else:
            with open(dataset_file, 'rb') as pkl:
                self.datasets = pickle.load(pkl)
            # load available datasets only when latest update is 1 months ago
            if (datetime.now() - self.datasets["latest_update"]) < timedelta(days = 30):
                load_from_web = False
            else:
                load_from_web = True

        if load_from_web:    
            url = "https://dataset.api.hub.zamg.ac.at/v1/datasets"
            request = requests.get(url).json()

            # creat window with progressbar
            prog = QProgressDialog(self.tr("Start Plugin\nLoad available datasets at first startup.."),None, 0, len(request))
            prog.setWindowModality(Qt.WindowModal)
                
            self.datasets = {}
            #some datasets require login, determine accessable datasets
            for i, (key, value) in enumerate(request.items()):
                prog.setValue(i)
                try:
                    requests.get(value["url"]).json()
                    typ = key.split("/")[1]
                    modus = key.split("/")[2]
                    id = key.split("/")[3]

                    if typ not in self.datasets.keys():
                        self.datasets[typ] = {}
                    if modus not in self.datasets[typ].keys():
                        self.datasets[typ][modus] = []
                    if id not in self.datasets[typ][modus]:
                        self.datasets[typ][modus].append(id)
                    
                    self.datasets.append(key)
                except:
                    pass
            self.datasets["latest_update"] = datetime.now()
            
            with open(dataset_file, 'wb') as pkl:
                pickle.dump(self.datasets, pkl, protocol=pickle.HIGHEST_PROTOCOL)

        del self.datasets["latest_update"]
        with QSignalBlocker(self.combobox_typ):
            self.combobox_typ.addItems(self.datasets.keys())
            self.update_modus()
        self.reset_ui()
    
    #update modus combobox
    def update_modus(self):
        with QSignalBlocker(self.combobox_modus):
            self.combobox_modus.clear()
            self.combobox_modus.addItems(self.datasets[self.combobox_typ.currentText()].keys())
            self.combobox_modus.setCurrentIndex(-1)
        with QSignalBlocker(self.combobox_id):
            self.combobox_id.clear()
        self.reset_ui()
  
    #update id combobox
    def update_id(self):
        with QSignalBlocker(self.combobox_id):
            self.combobox_id.clear()
            self.combobox_id.addItems(self.datasets[self.combobox_typ.currentText()][self.combobox_modus.currentText()])
            self.combobox_id.setCurrentIndex(-1)
            self.reset_ui()

    #reset ui objects when combobox type or mode is changed
    def reset_ui(self):
        self.button_load_stations.setEnabled(False)
        self.groupbox_stations.setEnabled(False)
        self.groupbox_points.setEnabled(False)
        self.extent.setEnabled(False)

        self.selected_parameters = []
        self.station_list = []
        #clear table stations
        self.table_stations.clear()
        self.table_stations.setRowCount(0)
        self.table_stations.setColumnCount(0)
        #clear table parameter
        self.table_parameters.clear()
        self.table_parameters.setRowCount(0)
        self.table_parameters.setColumnCount(0)
        #clear table points
        self.table_points.clear()
        self.table_points.setRowCount(0)
        self.table_points.setColumnCount(0)
        #clear combobox out format
        self.combobox_outformat.clear()
        self.button_download.setEnabled(False)
        #clear filter text
        with QSignalBlocker(self.parameter_filter):
            self.parameter_filter.setText("")
        
        if isinstance(self.iface.mapCanvas().mapTool(), QgsMapToolExtent):
            self.iface.mapCanvas().setMapTool(QgsMapToolPan(self.iface.mapCanvas()))

        #try to remove features if they exist
        try:
            del self.current_layer
        except:
            pass
        try:
            QgsProject.instance().removeMapLayer(self.point_layer)
            self.iface.mapCanvas().refresh()
        except:
            pass

    #load metadata from selected datapoint
    def load_metadata(self):
        url = f"https://dataset.api.hub.zamg.ac.at/v1/{self.combobox_typ.currentText()}/{self.combobox_modus.currentText()}/{self.combobox_id.currentText()}/metadata"
        request = requests.get(url)
        self.current_metadata = request.json()

        if self.combobox_typ.currentText() == "station":
            #enable select stations
            self.button_load_stations.setEnabled(True)
            self.groupbox_stations.setCollapsed(False)
            self.groupbox_stations.setEnabled(False)
            #disable select extent
            self.extent.setEnabled(False)
            self.extent.setCollapsed(True)
            #disable select point
            self.groupbox_points.setCollapsed(True)
            self.groupbox_points.setEnabled(False)
        elif self.combobox_typ.currentText() == "timeseries":
            #enable select point
            self.groupbox_points.setCollapsed(False)
            self.groupbox_points.setEnabled(True)
            #disable select extent
            self.extent.setEnabled(False)
            self.extent.setCollapsed(True)
            #disable select station
            self.button_load_stations.setEnabled(False)
            self.groupbox_stations.setCollapsed(True)
            self.groupbox_stations.setEnabled(False)
            
        else:
            #disable select station
            self.button_load_stations.setEnabled(False)
            self.groupbox_stations.setCollapsed(True)
            self.groupbox_stations.setEnabled(False)
            #disable select point
            self.groupbox_points.setCollapsed(True)
            self.groupbox_points.setEnabled(False)
            #enable select extent
            self.extent.setEnabled(True)
            self.extent.setCollapsed(False)
        
        self.update_parameters()
        self.selected_parameters = []
        self.table_stations.clear()
        self.table_stations.setRowCount(0)
        self.table_stations.setColumnCount(0)
        self.button_description.setEnabled(False)

        self.combobox_outformat.clear()
        formats = self.current_metadata["response_formats"]
        self.combobox_outformat.addItems(formats)
        if "csv" in formats:
            self.combobox_outformat.setCurrentIndex(formats.index("csv"))
        elif "netcdf" in formats:
            self.combobox_outformat.setCurrentIndex(formats.index("netcdf"))
        
        #set min and max dates to widget
        if self.current_metadata["mode"] != "current":
            strt_date = QDateTime.fromString(self.current_metadata["start_time"], Qt.ISODate)
            end_date = QDateTime.fromString(self.current_metadata["end_time"], Qt.ISODate)
            self.start_time.setMinimumDateTime(strt_date)
            self.end_time.setMinimumDateTime(strt_date)
            self.start_time.setMaximumDateTime(end_date)
            self.end_time.setMaximumDateTime(end_date)

            self.label_min_date.setText(f"(min: {strt_date.toString('dd.MM.yyyy HH:mm')} UTC)")
            self.label_max_date.setText(f"(max: {end_date.toString('dd.MM.yyyy HH:mm')}) UTC")

            #self.start_time.setDateTime(end_date)
            self.end_time.setDateTime(end_date)
        elif self.current_metadata["mode"] == "current":
            time = QDateTime(QDateTime.currentDateTimeUtc())
            self.start_time.setMinimumDateTime(time)
            self.end_time.setMinimumDateTime(time)
            self.start_time.setMaximumDateTime(time)
            self.end_time.setMaximumDateTime(time)
            self.label_min_date.setText(f"(min: {time.toString('dd.MM.yyyy HH:mm')} UTC)")
            self.label_max_date.setText(f"(max: {time.toString('dd.MM.yyyy HH:mm')} UTC)")

            self.start_time.setDateTime(time)
            self.end_time.setDateTime(time)
        
        self.button_download.setEnabled(True)
        self.button_description.setEnabled(True)
        self.iface.messageBar().pushInfo(self.tr("Finish"), self.tr("Loaded metadata from server."))

    def open_description_url(self):
        webbrowser.open(f"https://data.hub.zamg.ac.at/dataset/{self.combobox_id.currentText()}", new=2)

    #set filter on filewidget
    def update_filepath(self):
        if self.combobox_outformat.count() != 0:
            if self.combobox_outformat.currentText() == "csv":
                self.filewidget.setFilter("Comma-separated values (*.csv)")
            elif self.combobox_outformat.currentText() == "netcdf":
                self.filewidget.setFilter("NetCDF (*.nc)")
            elif self.combobox_outformat.currentText() == "geojson":
                self.filewidget.setFilter("GeoJson (*.json)")
            else:
                self.filewidget.setFilter("")

        if self.combobox_outformat.currentText() == "netcdf":
            self.checkbox_addLayer.setCheckState(True)
            self.checkbox_addLayer.setTristate(False)
        else:
            self.checkbox_addLayer.setCheckState(False)            

    #load all available stations to canvas
    def load_stations_to_canvas(self):
        self.groupbox_stations.setEnabled(True)
        df = pd.DataFrame.from_dict(self.current_metadata["stations"]).convert_dtypes()

        layer = QgsVectorLayer("point?crs=epsg:4326", "stationen", "memory")
        pr = layer.dataProvider()
        attributes = [] 
        for i, datatype in enumerate(df.dtypes):
            if df.columns[i] in ("valid_from","valid_to"):
                typ = QVariant.Date
            elif "float" in datatype.name.lower():
                typ = QVariant.Double
            elif "int" in datatype.name.lower():
                typ = QVariant.Int
            elif "boolean" in datatype.name.lower():
                typ = QVariant.Bool
            else:
                typ = QVariant.String
            
            attributes.append(QgsField(df.columns[i], typ))
        
        pr.addAttributes(attributes)
        layer.updateFields()

        for id,row in df.iterrows():
            feat = QgsFeature()
            # replave null values
            values = []
            for i,value in enumerate(row.tolist()):
                if pd.isna(value):
                    values.append(NULL)
                elif value == "":
                    if df.dtypes[i].name.lower() == "boolean":
                        values.append(False)
                    else:
                        values.append(NULL)
                else:
                    values.append(value)

            feat.setAttributes(values)
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(row.lon, row.lat)))
            with edit(layer):
                layer.addFeature(feat)

        alg_params = {
                    'INPUT': layer,
                    'OPERATION': '',
                    'TARGET_CRS': QgsCoordinateReferenceSystem.fromEpsgId(3857),
                    'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT}

        out = processing.run('native:reprojectlayer', alg_params)
        out["OUTPUT"].setName(self.combobox_id.currentText())
        qml = os.path.abspath(os.path.join(os.path.dirname(__file__),"layer_style","stations_points.qml"))
        out["OUTPUT"].loadNamedStyle(qml)
        self.current_layer = QgsProject.instance().addMapLayer(out["OUTPUT"])
        self.current_layer_idxName = self.current_layer.fields().names().index("name")
        self.current_layer_idxID = self.current_layer.fields().names().index("id")
        self.button_select.setEnabled(True)

    #select features on canvas by rectangle
    def get_features(self, rect):
        self.select_tool.clearRubberBand()
        
        sourceCrs = QgsProject.instance().crs()
        destCrs = QgsCoordinateReferenceSystem.fromEpsgId(3857)
        tr = QgsCoordinateTransform(sourceCrs, destCrs, QgsProject.instance())
        poly = QgsGeometry.fromRect(rect)
        poly.transform(tr)


        ids = [f.id() for f in self.current_layer.getFeatures() if f.geometry().intersects(poly)]
        station_ids = [f.attributes()[self.current_layer_idxID] for f in self.current_layer.getFeatures() if f.id() in ids]
        station_names = [f.attributes()[self.current_layer_idxName] for f in self.current_layer.getFeatures() if f.id() in ids]
        tab_display = [": ".join(i) for i in list(zip(station_ids,station_names))]
        self.current_layer.selectByIds(ids)

        current_len = self.table_stations.rowCount()
        
        if len(ids) != 0:
            self.table_stations.setColumnCount(2)
        
            for i in range(len(station_ids)):
                if station_ids[i] not in self.station_list:
                    current_len = self.table_stations.rowCount()
                    self.station_list.append(str(station_ids[i]))
                    self.table_stations.setRowCount(current_len + 1)
                    item_id = QTableWidgetItem()
                    item_id.setData(Qt.DisplayRole, station_ids[i])
                    self.table_stations.setItem(current_len,0,item_id)

                    item_name = QTableWidgetItem()
                    item_name.setData(Qt.DisplayRole, station_names[i])
                    self.table_stations.setItem(current_len,1,item_name)
            
            self.table_stations.setHorizontalHeaderLabels(["id","name"])
            self.table_stations.resizeColumnsToContents()
            self.table_stations.setSortingEnabled(True)

    #remove all stations in list
    def remove_all_entries(self,table):
        if table == self.table_stations:
            self.station_list = []
        table.clear()
        table.setRowCount(0)
        table.setColumnCount(0)

    #remove selected stations in list
    def remove_selected(self,table):
        rows = []
        remove = []
        for item in table.selectedItems():
            if table == self.table_stations:
                remove.append(self.table_stations.item(item.row(),0).text())
            rows.append(item.row())
        if table == self.table_stations:
            remove = self.unique(remove,sort = "descending")
            [self.station_list.remove(x) for x in remove]

        rows = self.unique(rows,sort = "descending")
        for row in rows:
            table.removeRow(row)

    #select parameter when checkbox is checked
    def select_parameters(self,parameter, checked):
        if checked:
            if parameter not in self.selected_parameters:
                self.selected_parameters.append(parameter)
        else:
            if parameter in self.selected_parameters:
                self.selected_parameters.remove(parameter)
    
    #method to get sorted list with unique values
    def unique(self,liste,sort = None):
        lst = list(set(liste))
        if sort == None:
            return lst
        elif sort == "ascending":
            return lst.sort()
        elif sort == "descending":
            lst.sort(reverse = True)
            return lst

    #update table with parameters
    def update_parameters(self):
        data = pd.DataFrame.from_dict(self.current_metadata["parameters"])

        self.setCursor(Qt.WaitCursor)
        
        self.table_parameters.clear()
        self.table_parameters.setSortingEnabled(False)
        self.table_parameters.setRowCount(0)
        self.table_parameters.setColumnCount(len(data.columns)+1)
        self.table_parameters.setRowCount(len(data.index))        
        for i, (index,row) in enumerate(data.iterrows()):
            select = QCheckBox()
            select.setStyleSheet("margin-left:10%; margin-right:10%;")
            select.stateChanged.connect(lambda state, parameter = row["name"]: self.select_parameters(parameter,state))
            select.setContentsMargins(0,0,0,0)
            self.table_parameters.setCellWidget(i, 0, select)

            for col,value in enumerate(row):
                if value != None and not pd.isnull(value):
                    item = QTableWidgetItem()
     
                    item.setData(Qt.DisplayRole, value)
                    self.table_parameters.setItem(i,col+1,item)

        header = [""]
        header.extend(data.columns.values)
        self.table_parameters.setHorizontalHeaderLabels(header)
        self.table_parameters.resizeColumnsToContents()
        self.table_parameters.setSortingEnabled(True)

        self.setCursor(Qt.ArrowCursor)

    #filter the parameter table, called when text in qlineedit is changed
    def filter_parameter_table(self):
        filter = self.parameter_filter.text().lower()
        for row in range(self.table_parameters.rowCount()):
            name = self.table_parameters.item(row,1).text().lower()
            desc = self.table_parameters.item(row,2).text().lower()
            if filter in name or filter in desc or filter == "":
                self.table_parameters.setRowHidden(row, False)
            else:
                self.table_parameters.setRowHidden(row, True)

    #unselect all parameters in table
    def unselect_all_parameters(self):
        for row in range(self.table_parameters.rowCount()):
            self.table_parameters.cellWidget(row,0).setCheckState(False)
            self.selected_parameters = []

    #select all parameters in current view
    def select_all_parameters(self):
        for row in range(self.table_parameters.rowCount()):
            if not self.table_parameters.isRowHidden(row):
                self.table_parameters.cellWidget(row,0).setCheckState(True)
                self.table_parameters.cellWidget(row,0).setTristate(False)
                self.selected_parameters.append(self.table_parameters.item(row,1).text())

    def select_point(self, point, mouse_button =None , crs = None):
        self.add_ts_point_layer()
        if not isinstance(point, QgsGeometry):
            pkt = QgsGeometry(QgsPoint(point))
        else:
            pkt = point
        if crs == None:
            sourceCrs = QgsProject.instance().crs()
        else:
            sourceCrs = crs
        destCrs = QgsCoordinateReferenceSystem.fromEpsgId(4326)
        tr = QgsCoordinateTransform(sourceCrs, destCrs, QgsProject.instance())
        pkt.transform(tr)

        #check if point exists in point layer
        for feat in self.point_layer.getFeatures():
            #check distance between points
            dist = feat.geometry().asPoint().distance(pkt.asPoint())
            if dist == 0:
                return
        new_feat = QgsFeature()
        new_feat.setGeometry(pkt)
        with QSignalBlocker(self.point_layer):
            with edit(self.point_layer):
                self.point_layer.addFeature(new_feat)
        self.point_layer.triggerRepaint()
        
        self.update_table_points()
    
    def update_table_points(self):
        self.table_points.setColumnCount(3)
        self.table_points.setRowCount(self.point_layer.featureCount())
        for i,feat in enumerate(self.point_layer.getFeatures()):
            #add lat
            item_lat = QTableWidgetItem()
            item_lat.setData(Qt.DisplayRole, "%.10f" % feat.geometry().asPoint().y())
            self.table_points.setItem(i,0,item_lat)
            #add lon
            item_lon = QTableWidgetItem()
            item_lon.setData(Qt.DisplayRole, "%.10f" % feat.geometry().asPoint().x())
            self.table_points.setItem(i,1,item_lon)
            #add id
            item_id = QTableWidgetItem()
            item_id.setData(Qt.DisplayRole, feat.id())
            self.table_points.setItem(i,2,item_id)
        self.table_points.setHorizontalHeaderLabels(["Lat","Lon","id"])
        self.table_points.setColumnHidden(2, True)
        self.table_points.resizeColumnsToContents()
        self.table_points.setSortingEnabled(True)

    def activate_select_point(self):
        try:
            if self.point_layer.isEditable():
                QMessageBox.warning(self,self.tr("Layer is editable"),self.tr("You are in editing mode.\nPlease use standard QGis tools."))
            else:
                self.iface.mapCanvas().setMapTool(self.point_tool)
        except:
            self.iface.mapCanvas().setMapTool(self.point_tool)

    def load_points_from_layer(self):            
        layer = self.combobox_pointlayer.currentLayer()
        if layer != None:
            if self.checkbox_onlySelected.isChecked():
                features = layer.selectedFeatures()
            else:
                features = layer.getFeatures()
            for f in features:
                    self.select_point(f.geometry(), crs = layer.crs())

    def add_ts_point_layer(self):
        try:
            self.point_layer.id()
        except:
            self.point_layer = QgsVectorLayer("point?crs=epsg:4326", "Timeseries Points", "memory")
            qml = os.path.abspath(os.path.join(os.path.dirname(__file__),"layer_style","ts_points.qml"))
            self.point_layer.loadNamedStyle(qml)
            self.point_layer.featureAdded.connect(self.update_table_points)
            self.point_layer.featuresDeleted.connect(self.update_table_points)
            self.point_layer.geometryChanged.connect(self.update_table_points)
            self.point_layer.editingStarted.connect(self.check_point_select_tool)
            QgsProject.instance().addMapLayer(self.point_layer)
    
    #when editing is startet change tool, creating point throws error when used in editing mode
    def check_point_select_tool(self):
        if self.iface.mapCanvas().mapTool() != None:
            if self.iface.mapCanvas().mapTool().toolName() == "geosphere_api_pointTool":
                self.iface.mapCanvas().setMapTool(QgsMapToolPan(self.iface.mapCanvas()))
    
    def delete_all_ts_points(self):
        with edit(self.point_layer):
            for feat in self.point_layer.getFeatures():
                self.point_layer.deleteFeature(feat.id())

    def delete_selected_ts_points(self):
        select = self.point_layer.getSelectedFeatures()
        with edit(self.point_layer):
            for feat in select:
                self.point_layer.deleteFeature(feat.id())

    #download data
    def download(self):
        if not os.path.isdir(os.path.dirname(self.filewidget.filePath())):
            QMessageBox.warning(self,self.tr("Missing filepath"),self.tr("Define storage location."))
            return
        self.setCursor(Qt.WaitCursor)
        parameter = ",".join(self.selected_parameters)
        strt = self.start_time.dateTime().toString("yyyy-MM-ddThh:mm")
        end = self.end_time.dateTime().toString("yyyy-MM-ddThh:mm")

        #check date input
        if strt == end and self.current_metadata["mode"] != "current":
            message_accepted =QMessageBox.warning(self,self.tr("Warning"),self.tr("Start and end date are ident.\nDo you want to continue"), QMessageBox.Yes|QMessageBox.No)
            if message_accepted ==  QMessageBox.No:
                self.setCursor(Qt.ArrowCursor)
                return
        #check selected format and filepath
        extension = os.path.splitext(self.filewidget.filePath())[1].replace(".","")
        out_format = self.combobox_outformat.currentText()
        if ((out_format == "csv" and extension != "csv") or \
            (out_format == "geojson" and (extension != "json" or extension != "geojson")) or \
            (out_format == "netcdf" and extension != "nc")):
            message_accepted =QMessageBox.warning(self,self.tr("Warning"),self.tr("File extension does not match selected output format.\nDo you want to continue?"), QMessageBox.Yes|QMessageBox.No)
            if message_accepted == QMessageBox.No:
                self.setCursor(Qt.ArrowCursor)
                return

        #download csv
        if self.combobox_typ.currentText() == "station":
            stations = ",".join(str(i) for i in self.station_list)
            url = f"https://dataset.api.hub.zamg.ac.at/v1/\
            {self.combobox_typ.currentText()}/\
            {self.combobox_modus.currentText()}/\
            {self.combobox_id.currentText()}\
            ?parameters={parameter}&start={strt}&end={end}\
            &station_ids={stations}&output_format={self.combobox_outformat.currentText()}".replace(" ","")
        
        elif self.combobox_typ.currentText() == "grid":
            coordinates = [
                self.extent.children()[2].children()[1].children()[3].text(),#south
                self.extent.children()[2].children()[1].children()[8].text(),#west
                self.extent.children()[2].children()[1].children()[2].text(),#north
                self.extent.children()[2].children()[1].children()[7].text(),#east
            ]
            coordinates = ",".join(coordinates)
            url = f"https://dataset.api.hub.zamg.ac.at/v1/\
            {self.combobox_typ.currentText()}/\
            {self.combobox_modus.currentText()}/\
            {self.combobox_id.currentText()}\
            ?parameters={parameter}&start={strt}&end={end}\
            &bbox={coordinates}&output_format={self.combobox_outformat.currentText()}".replace(" ","")
        
        elif self.combobox_typ.currentText() == "timeseries":
            if self.point_layer.isEditable():
                self.point_layer.commitChanges()
            points = []
            for feat in self.point_layer.getFeatures():
                lat = feat.geometry().asPoint().y()
                lon = feat.geometry().asPoint().x()
                "%.10f" % feat.geometry().asPoint().y()
                points.append("%.12f" % lat + "," + "%.12f" %  lon)

            points = "&lat_lon=".join(points)
            url = f"https://dataset.api.hub.zamg.ac.at/v1/\
            {self.combobox_typ.currentText()}/\
            {self.combobox_modus.currentText()}/\
            {self.combobox_id.currentText()}\
            ?parameters={parameter}&start={strt}&end={end}\
            &lat_lon={points}&output_format={self.combobox_outformat.currentText()}".replace(" ","")
        response = requests.get(url)
        print(url)
        if response.status_code == 200:
            content = response.content
            if self.combobox_outformat == "csv":
                content = content.replace(b",",b";").replace(b".",b",")
            try:
                with open(self.filewidget.filePath(), "wb") as file:
                    file.write(content)
            except:
                QMessageBox.warning(self,self.tr("Error"),self.tr("Could not write file.\nCheck path.."))
                return
        else:
            QMessageBox.warning(self,self.tr("Error"),self.tr("Download failed.\nCheck input variables..\nIf input variables ar correct the dataset may be too large."))
            self.setCursor(Qt.ArrowCursor)
            return

        path = self.filewidget.filePath().replace("\\","/")
        if self.checkbox_addLayer.isChecked():
            lname = os.path.splitext(os.path.basename(self.filewidget.filePath()))[0]
            if self.combobox_outformat.currentText() == "netcdf":
                uri=f'NETCDF:"{path}"'
                layer = QgsMeshLayer(uri,lname,"mdal")
            elif self.combobox_outformat.currentText() == "csv":
                layer = QgsVectorLayer(path, lname,"ogr")
            elif self.combobox_outformat.currentText() == "geojson":
                layer = QgsVectorLayer(f"{path}|layername={lname}", lname,"ogr")
            else:
                layer = QgsVectorLayer()
            if layer.isValid:
                QgsProject.instance().addMapLayer(layer)

        self.setCursor(Qt.ArrowCursor)
        self.iface.messageBar().pushSuccess(self.tr("Download finished"), f"File successfully saved in  <a href= '{os.path.dirname(path)}'> {path}  </a>")

