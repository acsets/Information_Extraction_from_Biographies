import re
import os
from pycorenlp import StanfordCoreNLP
nlp = StanfordCoreNLP('http://localhost:9000')
# opencc 用來繁簡轉換
from opencc import OpenCC
toTrad = OpenCC("s2t")
toSimp = OpenCC("t2s")
# 資料庫相關
from pymongo import MongoClient
client = MongoClient('localhost', 27017) # 建立對本地的Mongodb daemon 的連接
db = client['Summary'] # 接觸"Summary" 資料庫
db['relations'] # 如果不存在collection "relaitons" 則建立
#
from Utilities import parallelly_process, get_biography_text, get_people_in_text_within_people
from NER import KINSHIP_CHARS # 

def main():
    db.relations.remove() # 先清空舊的資料庫的relaitons
    update_kinships_to_db() # 先把之前在NER作的親屬關係暫存還原回來
    parallelly_process(main_process, list(db.biographies.find()))

# 先把之前在NER作的親屬關係暫存還原回來
def update_kinships_to_db():
    for person in db.people.find():
        for (aliasType, alias) in person['Alias_s']:
            if aliasType == "親屬關係暫存":
                biographee_name, kinship = alias.split(":")
                db.relations.insert_one({
                    'Name1' : biographee_name,
                    'Relation' : kinship,
                    'Name2' : person['Name'],
                })

def main_process(biographies):
    total_relations = []
    for biograpy in biographies:
        text = get_biography_text(biograpy)
        people = get_people_in_text_within_people(text, db.people.find()) # 找出傳記裡的所有登場人物
        names = get_all_names_of_people(people) # 取出所有登場人物的名子
        relations = []
        # 中間部份是別人寫的密密麻麻不知道在幹嘛，不想看不想註解
        for name in names:
            lines_have_name = extract_line(text, name)
            for line in lines_have_name:
                relations.extend(relationship(line, biograpy['Name'], name))
        relations = filter_relations(relations) # 過濾到自己跟自己的關係，和親屬關係(避免重複)
        output_relations_of_biography(relations, biograpy) # 將結果輸出成檔案方便看
        total_relations += relations
                
    update_relations_to_db(total_relations)

def get_all_names_of_people(people):
    names = []
    for person in people:
        names.append(person['Name'])
        for (aliasType, aliasName) in person['Alias_s']:
            names.append(aliasName)
    return names

def extract_line(corpus, name):
    corpus = corpus.replace("\n\n", "")
    corpus = re.split("，|。", corpus)
    corpus = list(filter(None, corpus))
    result = []
    for line in corpus:
        if name in line:
            if "（" in line:
                line = re.sub("（(.*?)）", "", line)
            result.append(line)
    return result

def relationship(text, main_char, obj):
    """
    test_txt = "被王小明殺害"
    relationship(test_txt, "王世慶", "王小明")
    >>> ['王小明 杀害 王世庆']
    
    test_txt = "和美國學者史威廉教授合作共同發表論文"
    relationship(test_txt, "王世慶", "史威廉")
    >>> ['王世慶 合作發表論文 史威廉']
    """
    text = toSimp.convert(text)
    main_char = toSimp.convert(main_char)
    obj = toSimp.convert(obj)
    dep_dict = build_dict(text)
    verb_output = []
    nn_output = []
    if obj in dep_dict.keys():
        if "nsubj" in dep_dict[obj]['dependency'].keys(): # 母亲为xxx / 父亲xx，也就是目標人名與某個詞有直接的主賓依賴關係
            sentence = '{} {} {}'.format(main_char, dep_dict[obj]['dependency']['nsubj'], obj)
            return [toTrad.convert(sentence)]
    for word in dep_dict:
        if dep_dict[word]['pos'] == 'VV': # Verb
            if (word not in obj) and (word not in main_char): # 確保斷詞不是人名的一部分
                word_deps = dep_dict[word]['dependency'].keys()
                if 'nsubj' in word_deps: # 如果該字前面有主語
                    nsubj = dep_dict[word]['dependency']['nsubj']
                    if 'dobj' in word_deps: # 如果該字後面有賓語
                        dobj = dep_dict[word]['dependency']['dobj']
                        if nsubj == main_char: # 如果該字前面的主語等於主要人物名稱，則不寫入
                            if dobj == obj:
                                verb_output.append('{} {} {}'.format(main_char, word, obj))
                            else:
                                verb_output.append('{} {}{} {}'.format(main_char, word, dobj, obj))
                        else:
                            if dobj == obj:
                                verb_output.append('{} {}{} {}'.format(main_char, nsubj, word, obj))
                            else:
                                verb_output.append('{} {}{}{} {}'.format(main_char, nsubj, word, dobj, obj))
                    else: # 在傳記文體中，如果該字前面有主語而無賓語，則可能是關係可能是 對方 -> action -> 主要人物
                        if (nsubj == obj) or (nsubj == main_char):
                            verb_output.append('{} {} {}'.format(obj, word, main_char))
                        else:
                            verb_output.append('{} {}{} {}'.format(obj, nsubj, word, main_char))
                else:
                    if 'dobj' in word_deps:
                        dobj = dep_dict[word]['dependency']['dobj']
                        if dobj == obj:
                            verb_output.append('{} {} {}'.format(main_char, word, obj))
                        else:
                            verb_output.append('{} {}{} {}'.format(main_char, word, dobj, obj))
                    else:
                        verb_output.append('{} {} {}'.format(main_char, word, obj))
            else: # 斷詞是人名的一部分，不處理。比如 中川
                None
        else: # not verb
            word_dep = dep_dict[word]['dependency']
            if "nmod:assmod" in word_dep.keys() and word_dep["nmod:assmod"] == obj: # 目標人名若是某個名詞的修飾詞
                sentence = '{} {} {}'.format(obj, word, main_char)  # 則很有可能代表關係的方向是 目標 -> 名詞 -> 主要人物
                return [toTrad.convert(sentence)]                    
            else:
                for dp in word_dep:
                    if dp == "case" and dep_dict[word]["pos"] == "NN": # 因美国学者田武雅教授的推荐
                        nn_output.append('{} {} {}'.format(obj, word, main_char))
                    elif dep_dict[word]['dependency'][dp] == obj:
                        nn_output.append('{} {} {}'.format(main_char, word, obj))
    if verb_output:
        verb_output = list(map(lambda x: toTrad.convert(x), verb_output))
        return verb_output
    elif nn_output:
        nn_output = list(map(lambda x: toTrad.convert(x), nn_output))
        return nn_output
    else:
        return "there has no relationships" ## be treated as list when extend the reture value of this func

def build_dict(text):
    result = dict()
    output = nlp.annotate(text, properties={
    'annotators': "tokenize, ssplit, pos, depparse",
    'outputFormat': 'json',
    })
    for sent in output['sentences']:
        for token in sent['tokens']:
            word = token['word']
            result[word] = {"pos": token['pos'], "dependency": {} }
        for dependency in sent['basicDependencies']:
            label = dependency['dep']
            if label != 'ROOT':
               parent_word = dependency['governorGloss']
               child_word = dependency['dependentGloss']
               result[parent_word]['dependency'][label] = child_word
               
    return result

def filter_relations(relations):
    filtered_relations = []
    for relation in relations:
        splits = relation.split()
        if len(splits) != 3:
            continue
        name1, rel, name2 = splits

        # filter out self pointed
        if name1 == name2:
            continue
        
        # filter out kinship
        isKinship = False
        for kinship in KINSHIP_CHARS:
            if kinship in rel:
                isKinship = True
                break
        if isKinship:
            continue
        
        filtered_relations.append("{} {} {}".format(name1, rel, name2))

    return filtered_relations

def output_relations_of_biography(relations, biography):
    try:
        os.makedirs('./DataBase/relation')
    except FileExistsError: # directory is exist
        pass

    for relationship in db.relations.find():
        if relationship['Name1'] == biography['Name']:
            relations.append( "{} {} {}".format(relationship['Name1'], relationship['Relation'], relationship['Name2']) )

    with open('./DataBase/relation/{}-{}-{}.txt'.format(biography['Book'], biography['StartPage'], biography['Name']), mode='w', encoding='utf-8') as f:
        for relation in relations:
            relation = relation.split()
            if isinstance(relation, list) and len(relation)==3:
                name1, rel, name2 = relation
                print(name1, rel, name2, file=f)

            
def update_relations_to_db(relations):
    for relation in relations:
        relation = relation.split()
        if isinstance(relation, list) and len(relation)==3:
            name1, rel, name2 = relation
            db.relations.insert_one(
                {'Name1' : name1,
                 'Relation' : rel,
                 'Name2' : name2,}
            )

if __name__=='__main__':
    main()    
